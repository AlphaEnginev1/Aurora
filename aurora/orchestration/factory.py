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
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter
from aurora.inference.discovery import ModelCandidateStore, ModelDiscoveryService
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.dashboard.gateway import DashboardGateway
from aurora.research.discovery import HttpSearchProvider, ResearchSourceCatalog
from aurora.research.models import ResearchRequest
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore
from aurora.context import AuroraContextPlane, ContextGate
from aurora.organization import AgentSelector, OrganizationRegistry
from aurora.projects import ProjectRegistry
from aurora.observability import ProductionObservability
from aurora.skills import SkillAcquisitionRuntime, SkillEvaluator, SkillEvolutionEngine, SkillLearningStore, SkillRegistry
from aurora.skills.acquisition_pipeline import SkillAcquisitionPipeline
from aurora.skills.autonomous_specialist_acquisition import AutonomousSpecialistAcquisition
from aurora.skills.evolution_runtime import SkillEvolutionRuntime
from aurora.skills.validation_lab import SkillValidationLab
from aurora.skills.improvement import SkillImprovementGenerator
from aurora.skills.discovery import SkillDiscoveryService
from aurora.operator_settings import OperatorSettingsStore, settings_path
from .director import DirectorPlanner, DirectorService
from .director_provider import InferenceDirectorProvider
from .gemini_provider import GeminiDirectorProvider
from .director_bootstrap import (
    DirectorBootstrap,
    build_context_knowledge_adapter,
    build_context_mailbox_adapter,
    build_context_runtime_adapter,
)
from .director_decision_knowledge_store import DirectorDecisionKnowledgeStore
from .director_decision_performance import DirectorDecisionPerformanceStore
from .director_decision_policy_active_store import DirectorDecisionPolicyActiveStore
from .director_decision_policy_contract_store import DirectorDecisionPolicyContractStore
from .director_decision_policy_proposal_store import DirectorDecisionPolicyProposalStore
from .durable_director import DurableDirectorService
from .engine import OrchestrationEngine
from .event_bus import DurableEventBus
from .mission_memory import MissionMemory
from .director_runtime import DirectorRuntime
from .director_conversation import DirectorConversationService
from .task_engine import TaskEngine
from .task_store import DurableTaskStore
from .team_runtime import TeamCoordinator
from .task_worker import WorkerPolicy

def build_orchestration_engine(policy_path: str | Path) -> OrchestrationEngine:
    nvidia_key = os.getenv("NVIDIA_API_KEY")
    provider_registry = default_provider_registry(nvidia_api_key=nvidia_key)
    model_registry = ModelRegistry.from_file(
        policy_path,
        provider_registry=provider_registry,
    )
    return OrchestrationEngine(
        ModelRouter(
            models=list(model_registry.models),
            nvidia_api_key=nvidia_key,
            provider_registry=provider_registry,
            model_registry=model_registry,
        )
    )

def _build_director_provider(settings_store: OperatorSettingsStore | None = None):
    settings = settings_store.load() if settings_store is not None else {}
    configured = settings.get("director", {}) if isinstance(settings, dict) else {}
    director_provider_id = os.getenv(
        "AURORA_DIRECTOR_PROVIDER",
        str(configured.get("provider", "router")),
    ).strip().lower()
    if director_provider_id == "gemini":
        key = (
            (settings_store.credential("gemini") if settings_store is not None else "")
            or os.getenv("GEMINI_API_KEY", "")
        )
        if not key and settings_store is not None:
            return None
        return GeminiDirectorProvider(
            api_key=key,
            model=os.getenv("GEMINI_MODEL_ID") or str(configured.get("model", "gemini-3.7-flash")),
            endpoint=os.getenv("GEMINI_API_ENDPOINT") or str(
                configured.get("endpoint", "https://generativelanguage.googleapis.com/v1beta")
            ),
        )
    if director_provider_id in {"router", "inference_fabric", "nvidia_api", ""}:
        return None
    raise RuntimeError(
        "unsupported AURORA_DIRECTOR_PROVIDER: "
        + director_provider_id
        + "; expected gemini or router"
    )


def build_director_service(*, inference_policy_path: str | Path, autonomy_policy_path: str | Path,
    registry_path: str | Path, network_policy_path: str | Path, workspace_root: str | Path,
    egress_proxy_host: str, egress_proxy_port: int=3128, organization_path: str | Path="config/organization.json",
    skills_path: str | Path="config/skills.json", skill_learning_path: str | Path | None=None,
    projects_path: str | Path="config/projects.json") -> DirectorService:
    settings_store = OperatorSettingsStore(settings_path(workspace_root))
    nvidia_key = settings_store.credential("nvidia_api") or os.getenv("NVIDIA_API_KEY")
    provider_registry = default_provider_registry(nvidia_api_key=nvidia_key)
    model_registry = ModelRegistry.from_file(
        inference_policy_path,
        provider_registry=provider_registry,
    )
    inference_specs = list(model_registry.models)
    router=ModelRouter(
        models=inference_specs,
        nvidia_api_key=nvidia_key,
        provider_registry=provider_registry,
        model_registry=model_registry,
    )
    skill_registry=SkillRegistry.from_file(skills_path,configured_models={model.id for model in inference_specs})
    organization=OrganizationRegistry.from_file(organization_path,configured_models={model.id for model in inference_specs})
    skill_registry.validate_organization(organization)
    project_registry=ProjectRegistry.from_file(
        projects_path,
        configured_agents=organization.agents_by_role().keys(),
        configured_skills=skill_registry.ids(),
        configured_models={model.id for model in inference_specs},
    )
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
    director_provider = _build_director_provider(settings_store)

    planner=DirectorPlanner(
        router,
        organization=organization,
        skill_registry=skill_registry,
        project_registry=project_registry,
        provider=director_provider or InferenceDirectorProvider(router),
    )
    return DirectorService(planner,task_engine)

def build_director_runtime(*, inference_policy_path: str | Path, autonomy_policy_path: str | Path,
    registry_path: str | Path, network_policy_path: str | Path, workspace_root: str | Path,
    egress_proxy_host: str, egress_proxy_port: int=3128, task_db_path: str | Path | None=None,
    worker_policy: WorkerPolicy | None=None, worker_id: str | None=None, worker_concurrency: int=4,
    organization_path: str | Path="config/organization.json", skills_path: str | Path="config/skills.json",
    skill_learning_path: str | Path | None=None,
    projects_path: str | Path="config/projects.json") -> DirectorRuntime:
    service=build_director_service(inference_policy_path=inference_policy_path,autonomy_policy_path=autonomy_policy_path,
        registry_path=registry_path,network_policy_path=network_policy_path,workspace_root=workspace_root,
        egress_proxy_host=egress_proxy_host,egress_proxy_port=egress_proxy_port,organization_path=organization_path,
        skills_path=skills_path,skill_learning_path=skill_learning_path,projects_path=projects_path)
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
    research_store=ResearchStore(runtime_root/"research.db")
    research_discovery_endpoint=os.getenv("AURORA_RESEARCH_DISCOVERY_ENDPOINT","").strip()
    research_discovery=(
        HttpSearchProvider(gateway,research_discovery_endpoint)
        if research_discovery_endpoint else None
    )
    research_service=ResearchService(gateway,research_store,discovery=research_discovery)
    model_candidate_store=ModelCandidateStore(runtime_root/"model-candidates.db")
    if service.planner.router.provider_registry is None or service.planner.router.model_registry is None:
        raise RuntimeError("model registries are not configured")
    model_discovery=ModelDiscoveryService(
        service.planner.router.provider_registry,
        service.planner.router.model_registry,
        model_candidate_store,
    )
    acquisition_pipeline=SkillAcquisitionPipeline(skill_acquisition,runtime_root/"skill-acquisition-artifacts.db")
    source_catalog=ResearchSourceCatalog.from_environment()

    def discover_research_sources(gap):
        objective=(
            f"Discover reliable procedures for specialist skill {gap.skill_id}. "
            f"Gap: {gap.reason}. Capabilities: {', '.join(gap.required_capabilities)}"
        )
        if research_service.discovery is not None:
            return research_service.discover(
                ResearchRequest(
                    request_id=f"skill-discovery-search-{gap.mission_id}-{gap.skill_id}",
                    objective=objective,
                    constraints=gap.required_capabilities,
                    max_sources=10,
                )
            )
        return source_catalog.discover(gap)

    skill_discovery=SkillDiscoveryService(
        research=research_service,
        source_discovery=discover_research_sources,
        tool_catalog=registry,
    )
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

    decision_performance=DirectorDecisionPerformanceStore(store.path)
    decision_knowledge_store=DirectorDecisionKnowledgeStore(store.path)
    active_policy_store=DirectorDecisionPolicyActiveStore(store.path)
    policy_proposal_store=DirectorDecisionPolicyProposalStore(store.path)
    policy_contract_store=DirectorDecisionPolicyContractStore(store.path)

    context_plane=AuroraContextPlane(
        project_registry=service.planner.project_registry,
        organization=service.planner.organization,
        skill_registry=service.planner.skill_registry,
        models=tuple(service.planner.router.models),
        model_registry=service.planner.router.model_registry,
        runtime_context=build_context_runtime_adapter(store),
        mailbox_context=build_context_mailbox_adapter(store),
        knowledge_context=build_context_knowledge_adapter(research_store),
    )
    context_gate=ContextGate(context_plane)
    service.planner.router.configure_context_gate(context_gate, enforce=True)
    bootstrap=DirectorBootstrap(
        context_gate=context_gate,
        model_policy_path=inference_policy_path,
        decision_performance_store=decision_performance,
        active_policy_store=active_policy_store,
        policy_proposal_store=policy_proposal_store,
        policy_contract_store=policy_contract_store,
        decision_knowledge_store=decision_knowledge_store,
    )
    bootstrap_state=bootstrap.initialize()
    conversation_service=DirectorConversationService(
        router=service.planner.router,
        bootstrap=bootstrap,
        db_path=runtime_root/"conversations.db",
        provider=service.planner.provider,
    )

    observability=ProductionObservability(runtime_root/"observability.jsonl")
    memory=MissionMemory(store)
    dashboard_gateway=DashboardGateway(
        project_registry=service.planner.project_registry,
        organization=service.planner.organization,
        skill_registry=service.planner.skill_registry,
        model_registry=service.planner.router.model_registry,
        provider_registry=service.planner.router.provider_registry,
        task_store=store,
        mission_memory=memory,
        model_performance=ModelPerformanceStore(runtime_root/"model-performance.db"),
        skill_learning=skill_learning,
        research_store=research_store,
        decision_performance=decision_performance,
        decision_knowledge=decision_knowledge_store,
        active_policy_store=active_policy_store,
        observability=observability,
        director_context=bootstrap_state.context,
        director_model_policy=bootstrap_state.director_model_policy,
    )

    runtime=DirectorRuntime(director=durable_director,store=store,event_bus=event_bus,worker_policy=worker_policy,
        worker_id=worker_id,worker_concurrency=worker_concurrency,skill_evaluator=skill_evaluator,
        observability=observability,
        memory=memory,
        skill_learning=skill_learning,skill_evolution=skill_evolution,
        skill_evolution_runtime=skill_evolution_runtime, skill_improvement_generator=skill_improvement_generator,
        autonomous_specialist_acquisition=autonomous_specialist_acquisition,
        decision_performance=decision_performance,
        decision_knowledge_store=decision_knowledge_store,
        active_policy_store=active_policy_store,
        policy_proposal_store=policy_proposal_store,
        policy_contract_store=policy_contract_store,
        model_discovery=model_discovery,
        conversation_service=conversation_service)
    runtime.bootstrap=bootstrap
    runtime.bootstrap_state=bootstrap_state
    return runtime

