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
from aurora.inference.models import ModelSpec
from aurora.inference.discovery import ModelCandidateStore, ModelDiscoveryService
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.dashboard.gateway import DashboardGateway
from aurora.research.discovery import HttpSearchProvider, ResearchSourceCatalog
from aurora.research.models import ResearchRequest
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore
from aurora.context import AuroraContextPlane, ContextGate
from aurora.workbench import UniversalWorkbench, WorkSessionStore
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
from .director_provider_failover import (
    BoundInferenceDirectorProvider,
    DirectorProviderCandidate,
    DirectorProviderFailoverController,
    DirectorProviderFailoverStore,
)
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
from .director_eligibility import DirectorEligibilityStore
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

def _apply_local_model_settings(router: ModelRouter, settings_store: OperatorSettingsStore) -> None:
    settings = settings_store.load()
    director = settings.get("director", {}) if isinstance(settings, dict) else {}
    provider_id = str(director.get("provider", "")).strip().lower()
    model_id = str(director.get("model", "")).strip()
    if provider_id != "nvidia_api" or not model_id:
        return
    base_url = str(
        settings.get("providers", {}).get("nvidia_api", {}).get(
            "base_url", "https://integrate.api.nvidia.com/v1"
        )
    ).rstrip("/")
    try:
        existing = router.model_registry.resolve(model_id)
    except Exception:
        existing = None
    if existing is None:
        model = ModelSpec(
            id=model_id,
            provider="nvidia_api",
            endpoint=base_url,
            capabilities=("chat", "reasoning", "tool_use"),
            context_window=131072,
            enabled=True,
        )
        router.model_registry = type(router.model_registry).from_specs(
            [*router.model_registry.models, model],
            provider_registry=router.provider_registry,
        )
        router.models = list(router.model_registry.models)
        return
    if not existing.enabled or existing.endpoint != base_url:
        updated = ModelSpec(
            id=existing.id,
            provider=existing.provider,
            endpoint=base_url,
            capabilities=existing.capabilities or ("chat", "reasoning", "tool_use"),
            context_window=existing.context_window or 131072,
            enabled=True,
            specialties=existing.specialties,
            quality_score=existing.quality_score,
            latency_score=existing.latency_score,
            cost_score=existing.cost_score,
            healthy=existing.healthy,
            available=existing.available,
        )
        router.model_registry.replace(updated)
        router.models = list(router.model_registry.models)


def _build_director_provider(
    settings_store: OperatorSettingsStore | None = None,
    *,
    router: ModelRouter | None = None,
):
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
    if director_provider_id == "nvidia_api":
        key = (
            (settings_store.credential("nvidia_api") if settings_store is not None else "")
            or os.getenv("NVIDIA_API_KEY", "")
        )
        model = str(configured.get("model", "")).strip()
        if not key or router is None or not model:
            return None
        return BoundInferenceDirectorProvider(
            router,
            provider_id="nvidia_api",
            model=model,
        )
    if director_provider_id in {"router", "inference_fabric", ""}:
        return None
    raise RuntimeError(
        "unsupported AURORA_DIRECTOR_PROVIDER: "
        + director_provider_id
        + "; expected gemini or router"
    )


def _build_director_provider_failover(
    *,
    settings_store: OperatorSettingsStore,
    router: ModelRouter,
    active_provider,
    db_path: str | Path,
):
    settings = settings_store.load()
    director = settings.get("director", {}) if isinstance(settings, dict) else {}
    failover = director.get("failover", {}) if isinstance(director, dict) else {}
    if not bool(failover.get("enabled", True)):
        return active_provider

    order = failover.get("order", ["nvidia_api", "router"])
    if not isinstance(order, list):
        raise RuntimeError("director.failover.order must be a list")

    candidates: list[DirectorProviderCandidate] = []
    active_id = str(getattr(active_provider, "provider_id", "") or "")
    nvidia_base_url = str(
        settings.get("providers", {}).get("nvidia_api", {}).get(
            "base_url", "https://integrate.api.nvidia.com/v1"
        )
    ).rstrip("/")

    nvidia_model = next(
        (
            model.id
            for model in router.available()
            if model.provider == "nvidia_api"
            and "reasoning" in model.capabilities
        ),
        None,
    )
    for provider_id in (str(item).strip().lower() for item in order):
        if provider_id == "nvidia_api":
            key = settings_store.credential("nvidia_api") or os.getenv("NVIDIA_API_KEY", "")
            if active_id == "nvidia_api" or not key or not nvidia_model:
                continue
            candidates.append(
                DirectorProviderCandidate(
                    provider_id="nvidia_api",
                    model=nvidia_model,
                    endpoint=nvidia_base_url,
                    factory=lambda r=router, m=nvidia_model: BoundInferenceDirectorProvider(
                        r, provider_id="nvidia_api", model=m
                    ),
                )
            )
        elif provider_id == "router":
            has_router_reasoning_model = any(
                "reasoning" in model.capabilities for model in router.available()
            )
            if active_id in {"router", "inference_router"} or not has_router_reasoning_model:
                continue
            candidates.append(
                DirectorProviderCandidate(
                    provider_id="router",
                    model=None,
                    endpoint=None,
                    factory=lambda r=router: BoundInferenceDirectorProvider(
                        r, provider_id="router", model=None
                    ),
                )
            )

    def activate(candidate: DirectorProviderCandidate, _provider) -> None:
        settings_store.update_from_form(
            {
                "director": {
                    "provider": candidate.provider_id,
                    "model": candidate.model or "router",
                    "endpoint": candidate.endpoint
                    or str(director.get("endpoint", "https://generativelanguage.googleapis.com/v1beta")),
                }
            }
        )

    return DirectorProviderFailoverController(
        active_provider=active_provider,
        candidates=tuple(candidates),
        store=DirectorProviderFailoverStore(str(db_path)),
        activate=activate,
    )


def build_director_service(*, inference_policy_path: str | Path, autonomy_policy_path: str | Path,
    registry_path: str | Path, network_policy_path: str | Path, workspace_root: str | Path,
    egress_proxy_host: str, egress_proxy_port: int=3128, organization_path: str | Path="config/organization.json",
    skills_path: str | Path="config/skills.json", skill_learning_path: str | Path | None=None,
    projects_path: str | Path="config/projects.json",
    provider_failover_db_path: str | Path | None = None,
) -> DirectorService:
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
    _apply_local_model_settings(router, settings_store)
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
        capability_runtime=capability_runtime,
    )
    workbench=UniversalWorkbench(
        repository_root=Path(__file__).resolve().parents[2],
        runtime_root=root,
        gateway=gateway,
        policy=policy,
        session_store=WorkSessionStore(root / "work-sessions.db"),
        audit_path=audit_path,
        resolution=resolution,
    )
    orchestration=OrchestrationEngine(
        router,
        capability_runtime=capability_runtime,
        workbench=workbench,
    )
    learning_path=Path(skill_learning_path).resolve() if skill_learning_path is not None else root/"skill-learning.db"
    learning_store=SkillLearningStore(learning_path)
    model_performance=ModelPerformanceStore(root/"model-performance.db")
    task_engine=TaskEngine(orchestration,root,organization=organization,skill_registry=skill_registry,skill_learning=learning_store,model_performance=model_performance)
    director_provider = _build_director_provider(settings_store, router=router)

    planner=DirectorPlanner(
        router,
        organization=organization,
        skill_registry=skill_registry,
        project_registry=project_registry,
        provider=director_provider or InferenceDirectorProvider(router),
    )
    active_provider = planner.provider
    planner.provider = _build_director_provider_failover(
        settings_store=settings_store,
        router=router,
        active_provider=active_provider,
        db_path=provider_failover_db_path or (Path(workspace_root).resolve() / "tasks.db"),
    )
    return DirectorService(planner,task_engine)

def build_director_runtime(*, inference_policy_path: str | Path, autonomy_policy_path: str | Path,
    registry_path: str | Path, network_policy_path: str | Path, workspace_root: str | Path,
    egress_proxy_host: str, egress_proxy_port: int=3128, task_db_path: str | Path | None=None,
    worker_policy: WorkerPolicy | None=None, worker_id: str | None=None, worker_concurrency: int=4,
    organization_path: str | Path="config/organization.json", skills_path: str | Path="config/skills.json",
    skill_learning_path: str | Path | None=None,
    projects_path: str | Path="config/projects.json") -> DirectorRuntime:
    runtime_store_path = Path(task_db_path).resolve() if task_db_path else Path(workspace_root).resolve()/"tasks.db"
    service=build_director_service(inference_policy_path=inference_policy_path,autonomy_policy_path=autonomy_policy_path,
        registry_path=registry_path,network_policy_path=network_policy_path,workspace_root=workspace_root,
        egress_proxy_host=egress_proxy_host,egress_proxy_port=egress_proxy_port,organization_path=organization_path,
        skills_path=skills_path,skill_learning_path=skill_learning_path,projects_path=projects_path,
        provider_failover_db_path=runtime_store_path)
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
    service.task_engine.orchestration._workbench.research = research_service
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
        mission_runner=service.run_mission,
        workbench=service.task_engine.orchestration._workbench,
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

    director_eligibility=DirectorEligibilityStore(store.path)
    runtime=DirectorRuntime(director=durable_director,store=store,event_bus=event_bus,
        director_eligibility=director_eligibility,worker_policy=worker_policy,
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
        dashboard_gateway=dashboard_gateway,
        conversation_service=conversation_service)
    runtime.bootstrap=bootstrap
    runtime.bootstrap_state=bootstrap_state
    runtime.workbench=service.task_engine.orchestration._workbench
    return runtime

