"""Construct Aurora orchestration and autonomous Director services."""
from __future__ import annotations
import json, os
from pathlib import Path
from aurora.failure_tracker import report_failure
from aurora.autonomy.acquisition import AcquisitionManager
from aurora.autonomy.runtime_bindings import build_autonomy_runtime_bindings
from aurora.autonomy.mission_manager import AutonomousMissionManager
from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.capability_runtime import CapabilityRuntime
from aurora.autonomy.capability_resolution import CapabilityResolutionRuntime, HttpCapabilityDiscoveryProvider
from aurora.autonomy.internet import InternetGateway, InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.autonomy.registry import CapabilityRegistryStore, load_registry, merge_registries
from aurora.autonomy.sandbox import SandboxExecutor, SandboxPolicy
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter
from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.free_policy import is_free_model
from aurora.inference.discovery import ModelCandidateStore, ModelDiscoveryService
from aurora.inference.autonomous_discovery import AutonomousDiscoveryRefreshService
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.inference.provider_health import ProviderHealthStore
from aurora.inference.reserve_pool import DynamicReservePool
from aurora.inference.ensemble import ModelEnsemble, ModelEnsembleStore
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
    provider_registry = default_provider_registry(
        nvidia_api_key=nvidia_key,
    )
    model_registry = ModelRegistry.from_file(
        policy_path,
        provider_registry=provider_registry,
    )
    policy_data = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    cost_policy = os.getenv("AURORA_INFERENCE_COST_POLICY", str(policy_data.get("cost_policy", "free_only"))).strip().lower()
    if cost_policy != "free_only":
        raise RuntimeError("Aurora canonical runtime requires cost_policy=free_only")
    runtime_root = Path(
        os.getenv("AURORA_WORKSPACE_ROOT", ".aurora-runtime")
    ).resolve()
    health_store = ProviderHealthStore(
        runtime_root / "provider-health.db",
        failure_threshold=int(os.getenv("AURORA_PROVIDER_HEALTH_FAILURE_THRESHOLD", "2")),
        base_cooldown_seconds=float(os.getenv("AURORA_PROVIDER_HEALTH_BASE_COOLDOWN_SECONDS", "15")),
        max_cooldown_seconds=float(os.getenv("AURORA_PROVIDER_HEALTH_MAX_COOLDOWN_SECONDS", "300")),
    )
    return OrchestrationEngine(
        ModelRouter(
            models=list(model_registry.models),
            nvidia_api_key=nvidia_key,
            provider_registry=provider_registry,
            model_registry=model_registry,
            health_store=health_store,
            cost_policy=cost_policy,
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
            pricing_tier=("free" if is_free_model("nvidia_api", model_id) else "unknown"),
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
            pricing_tier=existing.pricing_tier or ("free" if is_free_model("nvidia_api", existing.id) else "unknown"),
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
    # Persisted settings never override the canonical Router default.
    # Direct provider selection requires an explicit process-level opt-in.
    director_provider_id = os.getenv(
        "AURORA_DIRECTOR_PROVIDER",
        str(configured.get("provider", "router")),
    ).strip().lower()

    if director_provider_id in {"router", "inference_fabric", ""}:
        return None

    if director_provider_id == "nvidia_api":
        model = str(configured.get("model", "")).strip()
        if not model:
            return None
        if router is not None and getattr(router, "cost_policy", "all") == "free_only" and not is_free_model("nvidia_api", model):
            # Dynamically discovered NVIDIA Free Endpoint models are admitted by the
            # discovery/activation path; static models must still pass the free policy.
            if not (router.model_registry and any(
                item.id == model and item.provider == "nvidia_api" and item.pricing_tier == "free"
                for item in router.model_registry.models
            )):
                raise RuntimeError("free_only inference policy rejects NVIDIA model: " + model)
        if settings_store is None:
            key = os.getenv("NVIDIA_API_KEY", "")
        else:
            credential = getattr(settings_store, "credential", None)
            key = (
                credential("nvidia_api")
                if callable(credential)
                else ""
            ) or os.getenv("NVIDIA_API_KEY", "")
        if not key or router is None:
            return None
        return BoundInferenceDirectorProvider(
            router,
            provider_id="nvidia_api",
            model=model,
        )

    # Legacy/external provider values are deliberately ignored. Aurora always
    # falls back to its internal router, whose only external provider is NVIDIA.
    return None

def _build_director_provider_failover(
    *,
    settings_store: OperatorSettingsStore,
    router: ModelRouter,
    active_provider,
    db_path: str | Path,
    health_store: ProviderHealthStore | None = None,
):
    settings = settings_store.load()
    director = settings.get("director", {}) if isinstance(settings, dict) else {}
    failover = director.get("failover", {}) if isinstance(director, dict) else {}
    if not bool(failover.get("enabled", True)):
        return active_provider

    order = failover.get("order", ["nvidia_api"])
    if not isinstance(order, list):
        raise RuntimeError("director.failover.order must be a list")
    normalized_order = tuple(str(item).strip().lower() for item in order if str(item).strip().lower() == "nvidia_api")

    model_candidate_store = ModelCandidateStore(
        Path(db_path).resolve().parent / "model-candidates.db"
    )
    if router.provider_registry is None or router.model_registry is None:
        raise RuntimeError("model registries are not configured")

    def resolve_candidates(
        messages: list[dict[str, object]],
        requested_model: str | None,
        role: str,
    ) -> tuple[DirectorProviderCandidate, ...]:
        active_id = str(getattr(active_provider, "provider_id", "") or "")
        active_model_id = str(
            getattr(active_provider, "model", "") or requested_model or ""
        ).strip()
        pool_specs: list[ModelSpec] = []
        seen_models: set[tuple[str, str]] = set()

        credentials = {
            "nvidia_api": settings_store.credential("nvidia_api") or os.getenv("NVIDIA_API_KEY", ""),
        }

        discovery = ModelDiscoveryService(
            router.provider_registry,
            router.model_registry,
            model_candidate_store,
        )

        for provider_id in normalized_order:
            if provider_id != "nvidia_api":
                continue
            if provider_id == active_id and active_model_id:
                # Keep every other NVIDIA model eligible when the active model fails.
                pass
            if provider_id not in credentials or not credentials[provider_id]:
                continue
            refresh_interval = float(
                os.getenv("AURORA_MODEL_DISCOVERY_REFRESH_SECONDS", "900")
            )
            if hasattr(discovery, "refresh_all"):
                refreshed = discovery.refresh_all(
                    provider_ids=(provider_id,),
                    min_interval_seconds=refresh_interval,
                )
                discovered = refreshed.get(provider_id, ())
            else:
                discovered = discovery.discover_and_stage(provider_id)
            if not discovered:
                # Governed last-known discovery remains a valid reserve snapshot.
                discovered = model_candidate_store.list(
                    provider_id=provider_id,
                    limit=500,
                )
            if not discovered:
                continue
            provider_spec = router.provider_registry.resolve(provider_id)
            for item in discovered:
                model = item.model
                capabilities = tuple(model.capabilities)
                if "reasoning" not in capabilities:
                    continue
                key = (model.provider, model.id)
                if (
                    key == (active_id, active_model_id)
                    or key in seen_models
                ):
                    continue
                seen_models.add(key)
                pool_specs.append(
                    ModelSpec(
                        id=model.id,
                        provider=model.provider,
                        endpoint=model.endpoint,
                        capabilities=capabilities,
                        context_window=model.context_window,
                        enabled=True,
                        specialties=model.specialties,
                        quality_score=model.quality_score,
                        latency_score=model.latency_score,
                        cost_score=model.cost_score,
                        healthy=True,
                        pricing_tier=model.pricing_tier,
                        available=True,
                    )
                )

        # Existing configured NVIDIA models remain reserve candidates; discovery is
        # still the preferred source and never creates a hard seven-model ceiling.
        for model in router.available():
            if model.provider != "nvidia_api":
                continue
            if active_id == model.provider and active_model_id == model.id:
                continue
            if "reasoning" not in model.capabilities:
                continue
            key = (model.provider, model.id)
            if key in seen_models:
                continue
            seen_models.add(key)
            pool_specs.append(model)

        if not pool_specs:
            return ()

        pool_registry = ModelRegistry.from_specs(
            pool_specs,
            provider_registry=router.provider_registry,
        )
        # DynamicReservePool is the single reserve-selection layer. It reuses
        # the canonical router's policy/scoring and health store; the failover
        # controller remains responsible only for execution and activation.
        pool_router = ModelRouter(
            models=list(pool_registry.models),
            nvidia_api_key=credentials["nvidia_api"],
            provider_registry=router.provider_registry,
            model_registry=pool_registry,
            context_gate=router.context_gate,
            enforce_context=router.enforce_context,
            health_store=router.health_store,
            cost_policy=getattr(router, "cost_policy", "all"),
        )
        selection_request = InferenceRequest(
            messages=messages,
            model=None,
            metadata={"role": role, "allowed_models": [model.id for model in pool_specs]},
        )
        reserve_pool = DynamicReservePool(router, max_candidates=500)
        try:
            reserve_candidates = reserve_pool.build(
                selection_request,
                models=pool_specs,
                capability="reasoning",
                exclude=((active_id, active_model_id),) if active_model_id else (),
            )
        except Exception as exc:
            report_failure("failover_reserve_pool", exc)
            return ()

        candidates: list[DirectorProviderCandidate] = []
        for reserve_candidate in reserve_candidates:
            selected = reserve_candidate.model
            candidates.append(
                DirectorProviderCandidate(
                    provider_id=selected.provider,
                    model=selected.id,
                    endpoint=selected.endpoint,
                    factory=lambda r=pool_router, m=selected: BoundInferenceDirectorProvider(
                        r,
                        provider_id=m.provider,
                        model=m.id,
                    ),
                )
            )
        return tuple(candidates)

    def activate(candidate: DirectorProviderCandidate, _provider) -> None:
        settings_store.update_from_form(
            {
                "director": {
                    "provider": candidate.provider_id,
                    "model": candidate.model or "router",
                    "endpoint": candidate.endpoint
                    or str(
                        director.get(
                            "endpoint",
                            "https://integrate.api.nvidia.com/v1",
                        )
                    ),
                }
            }
        )

    return DirectorProviderFailoverController(
        active_provider=active_provider,
        candidates=(),
        store=DirectorProviderFailoverStore(str(db_path)),
        activate=activate,
        candidate_resolver=resolve_candidates,
        health_store=health_store,
    )



def _activate_selected_discovered_models(
    router: ModelRouter,
    settings_store: OperatorSettingsStore,
    workspace_root: Path,
) -> None:
    """Make persisted/live free NVIDIA discovery available before Director startup.

    The Director may be configured to use a model learned from NVIDIA's live Free
    Endpoint catalog. Startup must therefore activate governed discovered candidates
    before validating the configured Director model; otherwise a valid dynamic model
    is rejected merely because the static seed policy does not list it.
    """
    settings = settings_store.load()
    director = settings.get("director", {}) if isinstance(settings, dict) else {}
    provider_id = str(director.get("provider", "router")).strip().lower()
    model_id = str(director.get("model", "")).strip()
    if not router.provider_registry or not router.model_registry:
        return
    if "nvidia_api" not in router.provider_registry.ids:
        return
    if not (settings_store.credential("nvidia_api") or os.getenv("NVIDIA_API_KEY", "")):
        return
    candidate_store = ModelCandidateStore(workspace_root / "model-candidates.db")
    discovery = ModelDiscoveryService(
        router.provider_registry,
        router.model_registry,
        candidate_store,
    )
    # Reuse the governed refresh interval. If the catalog was refreshed recently,
    # refresh_all returns the persisted candidates without another network call.
    discovery.refresh_all(
        provider_ids=("nvidia_api",),
        min_interval_seconds=float(os.getenv("AURORA_MODEL_DISCOVERY_REFRESH_SECONDS", "900")),
    )
    if hasattr(discovery, "activate_eligible_discovered"):
        discovery.activate_eligible_discovered(
            provider_id="nvidia_api",
            cost_policy=getattr(router, "cost_policy", "free_only"),
            health_store=getattr(router, "health_store", None),
            limit=500,
        )
    else:
        # Backward-compatible test/double seam; production discovery always
        # exposes the health-aware activation contract.
        discovery.activate_free_discovered(provider_id="nvidia_api", limit=500)

def build_director_service(*, inference_policy_path: str | Path, autonomy_policy_path: str | Path,
    registry_path: str | Path, network_policy_path: str | Path, workspace_root: str | Path,
    egress_proxy_host: str, egress_proxy_port: int=3128, organization_path: str | Path="config/organization.json",
    skills_path: str | Path="config/skills.json", skill_learning_path: str | Path | None=None,
    projects_path: str | Path="config/projects.json",
    provider_failover_db_path: str | Path | None = None,
) -> DirectorService:
    settings_store = OperatorSettingsStore(settings_path(workspace_root))
    gateway = InternetGateway(InternetPolicy.from_file(network_policy_path))
    nvidia_key = settings_store.credential("nvidia_api") or os.getenv("NVIDIA_API_KEY")
    provider_registry = default_provider_registry(
        nvidia_api_key=nvidia_key,
        network_gateway=gateway,
    )
    model_registry = ModelRegistry.from_file(
        inference_policy_path,
        provider_registry=provider_registry,
    )
    policy_data = json.loads(Path(inference_policy_path).read_text(encoding="utf-8"))
    cost_policy = os.getenv("AURORA_INFERENCE_COST_POLICY", str(policy_data.get("cost_policy", "free_only"))).strip().lower()
    if cost_policy != "free_only":
        raise RuntimeError("Aurora canonical runtime requires cost_policy=free_only")
    inference_specs = list(model_registry.models)
    health_store = ProviderHealthStore(
        Path(workspace_root).resolve() / "tasks.db",
        failure_threshold=int(os.getenv("AURORA_PROVIDER_HEALTH_FAILURE_THRESHOLD", "2")),
        base_cooldown_seconds=float(os.getenv("AURORA_PROVIDER_HEALTH_BASE_COOLDOWN_SECONDS", "15")),
        max_cooldown_seconds=float(os.getenv("AURORA_PROVIDER_HEALTH_MAX_COOLDOWN_SECONDS", "300")),
    )
    router=ModelRouter(
        models=inference_specs,
        nvidia_api_key=nvidia_key,
        provider_registry=provider_registry,
        model_registry=model_registry,
        health_store=health_store,
        cost_policy=cost_policy,
    )
    _apply_local_model_settings(router, settings_store)
    root=Path(workspace_root).resolve()
    _activate_selected_discovered_models(router, settings_store, root)
    # Dynamic NVIDIA discovery is authoritative for the active free-model pool;
    # downstream registries must see the post-discovery model set, not the stale
    # checked-in seed snapshot captured before activation.
    inference_specs = list(router.model_registry.models)
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
    workbench=UniversalWorkbench(
        repository_root=Path(__file__).resolve().parents[2],
        runtime_root=root,
        gateway=gateway,
        policy=policy,
        session_store=WorkSessionStore(root / "work-sessions.db"),
        audit_path=audit_path,
        resolution=resolution,
        capability_runtime=capability_runtime,
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
        health_store=health_store,
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
    autonomous_discovery_refresh=AutonomousDiscoveryRefreshService(
        discovery=model_discovery,
        router=service.planner.router,
        health_store=service.planner.router.health_store,
        interval_seconds=float(os.getenv("AURORA_MODEL_DISCOVERY_REFRESH_SECONDS", "900")),
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
    model_ensemble=ModelEnsemble(
        service.planner.router,
        store=ModelEnsembleStore(store.path),
    )
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
        autonomous_discovery_refresh=autonomous_discovery_refresh,
        model_discovery_refresh_enabled=(
            os.getenv("AURORA_MODEL_DISCOVERY_REFRESH_ENABLED", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        model_discovery_refresh_interval_seconds=float(
            os.getenv("AURORA_MODEL_DISCOVERY_REFRESH_SECONDS", "900")
        ),
        model_ensemble=model_ensemble,
        dashboard_gateway=dashboard_gateway,
        conversation_service=conversation_service)
    runtime.bootstrap=bootstrap
    runtime.bootstrap_state=bootstrap_state
    runtime.workbench=service.task_engine.orchestration._workbench
    # Expose the autonomous mission controller on the canonical runtime. It
    # delegates to this exact DirectorRuntime, preserving durable mission/task
    # identity, ownership, event bus, and terminal-state semantics.
    runtime.autonomous_mission_manager = AutonomousMissionManager(runtime)
    runtime.autonomy_controllers = build_autonomy_runtime_bindings(
        director_service=service,
        router=service.planner.router,
        workbench=runtime.workbench,
        research_service=research_service,
        skill_learning=skill_learning,
    )
    runtime.autonomous_computer_use = runtime.autonomy_controllers["computer_use"]
    runtime.autonomous_software_engineer = runtime.autonomy_controllers["software_engineer"]
    runtime.autonomous_discovery_loop = runtime.autonomy_controllers["discovery_loop"]
    runtime.recursive_self_improvement = runtime.autonomy_controllers["recursive_self_improvement"]
    return runtime