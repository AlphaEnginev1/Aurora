"""Construct Aurora orchestration and autonomous Director services."""
from __future__ import annotations
import json, os
from pathlib import Path
from aurora.autonomy.acquisition import AcquisitionManager
from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.capability_runtime import CapabilityRuntime
from aurora.autonomy.capability_resolution import CapabilityResolutionRuntime, HttpCapabilityDiscoveryProvider
from aurora.autonomy.internet import InternetGateway, InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.autonomy.registry import CapabilityRegistryStore, load_registry, merge_registries
from aurora.autonomy.sandbox import SandboxExecutor, SandboxPolicy
from aurora.inference.config import load_model_specs
from aurora.inference.router import ModelRouter
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.research.discovery import ResearchSourceCatalog
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore
from aurora.organization import AgentSelector, OrganizationRegistry
from aurora.skills import SkillAcquisitionRuntime, SkillEvaluator, SkillEvolutionEngine, SkillLearningStore, SkillRegistry
from aurora.skills.acquisition_pipeline import SkillAcquisitionPipeline
from aurora.skills.autonomous_specialist_acquisition import AutonomousSpecialistAcquisition
from aurora.skills.evolution_runtime import SkillEvolutionRuntime
from aurora.skills.validation_lab import SkillValidationLab
from aurora.skills.improvement import SkillImprovementGenerator
from aurora.skills.discovery import SkillDiscoveryService
from .director import DirectorPlanner, DirectorService
from .durable_director import DurableDirectorService
from .engine import OrchestrationEngine
from .event_bus import DurableEventBus
from .director_runtime import DirectorRuntime
from .task_engine import TaskEngine
from .task_store import DurableTaskStore
from .team_runtime import TeamCoordinator
from .task_worker import WorkerPolicy

def build_orchestration_engine(policy_path: str | Path) -> OrchestrationEngine:
    models=load_model_specs(policy_path)
    return OrchestrationEngine(ModelRouter(models=models,nvidia_api_key=os.getenv("NVIDIA_API_KEY")))

def build_director_service(*, inference_policy_path: str | Path, autonomy_policy_path: str | Path,
    registry_path: str | Path, network_policy_path: str | Path, workspace_root: str | Path,
    egress_proxy_host: str, egress_proxy_port: int=3128, organization_path: str | Path="config/organization.json",
    skills_path: str | Path="config/skills.json", skill_learning_path: str | Path | None=None) -> DirectorService:
    inference_specs=load_model_specs(inference_policy_path)
    router=ModelRouter(models=inference_specs,nvidia_api_key=os.getenv("NVIDIA_API_KEY"))
    skill_registry=SkillRegistry.from_file(skills_path,configured_models={model.id for model in inference_specs})
    organization=OrganizationRegistry.from_file(organization_path,configured_models={model.id for model in inference_specs})
    skill_registry.validate_organization(organization)
    autonomy_data=json.loads(Path(autonomy_policy_path).read_text(encoding="utf-8"))
    policy=Policy(allowed_capabilities=frozenset(autonomy_data.get("allowed_capabilities",[])),
                  allowed_sources=frozenset(autonomy_data.get("allowed_sources",[])),
                  require_verification=bool(autonomy_data.get("require_verification",True)))
    root=Path(workspace_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    dynamic_registry=CapabilityRegistryStore(root/"capability-registry.json")
    registry=merge_registries(registry_path,dynamic_registry.path)
    gateway=InternetGateway(InternetPolicy.from_file(network_policy_path))
    install_root=root/"managed-site"; transaction_dir=root/"transactions"; audit_path=root/"audit.jsonl"
    sandbox_policy=SandboxPolicy(network=False,timeout_seconds=120,cpu_seconds=60,memory_bytes=1_073_741_824,
        max_processes=64,max_open_files=256,max_output_bytes=1_048_576,
        allowed_env=frozenset({"PATH","LANG","LC_ALL","TZ"}),readonly_system=True)
    offline_sandbox=SandboxExecutor(sandbox_policy)
    network_sandbox=SandboxExecutor(SandboxPolicy(network=True,egress_proxy_host=egress_proxy_host,
        egress_proxy_port=egress_proxy_port,timeout_seconds=120,cpu_seconds=60,memory_bytes=1_073_741_824,
        max_processes=64,max_open_files=256,max_output_bytes=1_048_576,
        allowed_env=frozenset({"PATH","LANG","LC_ALL","TZ"}),readonly_system=True))
    acquisition=AcquisitionManager(allowed_sources=policy.allowed_sources,audit_path=audit_path,
        install_root=install_root,transaction_dir=transaction_dir,internet_gateway=gateway,
        sandbox=offline_sandbox,network_sandbox=network_sandbox)
    broker=CapabilityBroker(policy=policy,registry=registry,acquisition=acquisition)
    discovery_endpoint=os.getenv("AURORA_CAPABILITY_DISCOVERY_ENDPOINT")
    discovery=(
        HttpCapabilityDiscoveryProvider(discovery_endpoint,gateway)
        if discovery_endpoint else None
    )
    resolution=CapabilityResolutionRuntime(
        broker,
        discovery=discovery,
        registrar=dynamic_registry.register,
        audit_path=audit_path,
    )
    capability_runtime=CapabilityRuntime(
        broker,
        offline_sandbox,
        audit_path=audit_path,
        resolution=resolution,
    )
    orchestration=OrchestrationEngine(router,capability_runtime=capability_runtime)
    learning_path=Path(skill_learning_path).resolve() if skill_learning_path is not None else root/"skill-learning.db"
    learning_store=SkillLearningStore(learning_path)
    model_performance=ModelPerformanceStore(root/"model-performance.db")
    task_engine=TaskEngine(orchestration,root,organization=organization,skill_registry=skill_registry,skill_learning=learning_store,model_performance=model_performance)
    planner=DirectorPlanner(router,organization=organization,skill_registry=skill_registry)
    return DirectorService(planner,task_engine)

def build_director_runtime(*, inference_policy_path: str | Path, autonomy_policy_path: str | Path,
    registry_path: str | Path, network_policy_path: str | Path, workspace_root: str | Path,
    egress_proxy_host: str, egress_proxy_port: int=3128, task_db_path: str | Path | None=None,
    worker_policy: WorkerPolicy | None=None, worker_id: str | None=None, worker_concurrency: int=4,
    organization_path: str | Path="config/organization.json", skills_path: str | Path="config/skills.json",
    skill_learning_path: str | Path | None=None) -> DirectorRuntime:
    service=build_director_service(inference_policy_path=inference_policy_path,autonomy_policy_path=autonomy_policy_path,
        registry_path=registry_path,network_policy_path=network_policy_path,workspace_root=workspace_root,
        egress_proxy_host=egress_proxy_host,egress_proxy_port=egress_proxy_port,organization_path=organization_path,
        skills_path=skills_path,skill_learning_path=skill_learning_path)
    runtime_root=Path(workspace_root).resolve()
    autonomy_data=json.loads(Path(autonomy_policy_path).read_text(encoding="utf-8"))
    policy=Policy(allowed_capabilities=frozenset(autonomy_data.get("allowed_capabilities",[])),
                  allowed_sources=frozenset(autonomy_data.get("allowed_sources",[])),
                  require_verification=bool(autonomy_data.get("require_verification",True)))
    dynamic_registry=CapabilityRegistryStore(runtime_root/"capability-registry.json")
    registry=merge_registries(registry_path,dynamic_registry.path)
    gateway=InternetGateway(InternetPolicy.from_file(network_policy_path))
    install_root=runtime_root/"managed-site"
    transaction_dir=runtime_root/"transactions"
    audit_path=runtime_root/"audit.jsonl"
    offline_sandbox=SandboxExecutor(SandboxPolicy(
        network=False,timeout_seconds=120,cpu_seconds=60,memory_bytes=1_073_741_824,
        max_processes=64,max_open_files=256,max_output_bytes=1_048_576,
        allowed_env=frozenset({"PATH","LANG","LC_ALL","TZ"}),readonly_system=True))
    network_sandbox=SandboxExecutor(SandboxPolicy(
        network=True,egress_proxy_host=egress_proxy_host,egress_proxy_port=egress_proxy_port,
        timeout_seconds=120,cpu_seconds=60,memory_bytes=1_073_741_824,
        max_processes=64,max_open_files=256,max_output_bytes=1_048_576,
        allowed_env=frozenset({"PATH","LANG","LC_ALL","TZ"}),readonly_system=True))
    acquisition=AcquisitionManager(allowed_sources=policy.allowed_sources,audit_path=audit_path,
        install_root=install_root,transaction_dir=transaction_dir,internet_gateway=gateway,
        sandbox=offline_sandbox,network_sandbox=network_sandbox)
    store_path=Path(task_db_path).resolve() if task_db_path else runtime_root/"tasks.db"
    store=DurableTaskStore(store_path); event_bus=DurableEventBus(store)
    skill_acquisition=SkillAcquisitionRuntime(runtime_root/"skill-acquisition.db")
    research_service=ResearchService(gateway,ResearchStore(runtime_root/"research.db"))
    acquisition_pipeline=SkillAcquisitionPipeline(skill_acquisition,runtime_root/"skill-acquisition-artifacts.db")
    source_catalog=ResearchSourceCatalog.from_environment()
    skill_discovery=SkillDiscoveryService(research=research_service,source_discovery=source_catalog.discover,tool_catalog=registry)
    autonomous_specialist_acquisition=AutonomousSpecialistAcquisition(
        research=research_service,acquisition=acquisition,pipeline=acquisition_pipeline,
        sandbox=offline_sandbox,discovery=skill_discovery)
    team_coordinator=TeamCoordinator(task_engine=service.task_engine,
        agent_selector=AgentSelector(service.task_engine.organization,service.task_engine.skill_registry,service.task_engine.skill_learning),
        skill_acquisition=skill_acquisition)
    durable_director=DurableDirectorService(planner=service.planner,task_engine=service.task_engine,
        store=store,event_bus=event_bus,team_coordinator=team_coordinator)
    skill_learning=service.task_engine.skill_learning
    if not isinstance(skill_learning,SkillLearningStore): raise RuntimeError("skill learning store is not configured")
    skill_registry=service.task_engine.skill_registry
    if skill_registry is None: raise RuntimeError("skill registry is not configured")
    skill_evaluator=SkillEvaluator(skill_registry); skill_evolution=SkillEvolutionEngine(skill_learning)
    skill_validation_lab=SkillValidationLab(service.planner.router)
    skill_evolution_runtime=SkillEvolutionRuntime(skill_learning, runtime_root/"skill-evolution.db", validation_lab=skill_validation_lab)
    skill_improvement_generator=SkillImprovementGenerator(service.planner.router, skill_learning)
    return DirectorRuntime(director=durable_director,store=store,event_bus=event_bus,worker_policy=worker_policy,
        worker_id=worker_id,worker_concurrency=worker_concurrency,skill_evaluator=skill_evaluator,
        skill_learning=skill_learning,skill_evolution=skill_evolution,
        skill_evolution_runtime=skill_evolution_runtime, skill_improvement_generator=skill_improvement_generator,
        autonomous_specialist_acquisition=autonomous_specialist_acquisition)

