"""LIVE acceptance for Aurora's free-model inference fabric.

The gate uses the free Gemini tier and NVIDIA Free Endpoint catalog discovered
from the official NVIDIA catalog. It does not require paid OpenAI/Anthropic credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from aurora.inference.discovery import ModelCandidateStore, ModelDiscoveryService
from aurora.inference.ensemble import ModelEnsemble, ModelEnsembleStore
from aurora.inference.free_policy import is_free_model, record_metadata
from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.provider_health import ProviderHealthStore
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter
from aurora.operator_settings import OperatorSettingsStore
from aurora.organization import AgentSelector, AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director import DirectorPlan
from aurora.orchestration.director_provider_failover import DirectorProviderFailoverStore
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import OrchestrationEngine
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.factory import _build_director_provider_failover
from aurora.orchestration.gemini_provider import GeminiDirectorProvider
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.team_runtime import TeamCoordinator

LIVE_FLAG = "AURORA_MULTI_PROVIDER_LIVE"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise AssertionError(f"live free-model gate requires {name}")
    return value


@dataclass
class OneShotPrimary:
    provider: object

    def __post_init__(self) -> None:
        self.calls = 0
        self.provider_id = str(getattr(self.provider, "provider_id", ""))

    def generate(self, *, messages, model, role, tools=()):
        self.calls += 1
        if self.calls == 1:
            return self.provider.generate(
                messages=messages,
                model=model,
                role=role,
                tools=tools,
            )
        error = RuntimeError("controlled live primary provider outage")
        error.failoverable = True
        raise error


class LivePlanner:
    provider = None

    def plan(self, *, objective, context=None, model=None):
        return DirectorPlan(
            objective=objective,
            tasks=(
                DelegatedTask(
                    "seed-review",
                    "researcher",
                    "Keep one concise evidence item for review.",
                ),
            ),
            model=model or "live/planner",
        )


def _free_nvidia_candidates(discovered) -> list[ModelSpec]:
    by_id = {}
    for item in discovered:
        model = item.model
        if model.pricing_tier == "free":
            by_id[model.id] = model
    return [by_id[model_id] for model_id in sorted(by_id)]


def _override_or_free(
    provider_id: str,
    env_name: str,
    discovered,
    *,
    endpoint: str,
) -> ModelSpec:
    override = os.getenv(env_name, "").strip()
    if override:
        if not is_free_model(provider_id, override):
            raise AssertionError(
                f"{env_name} must reference a model explicitly authorized as free"
            )
        metadata = record_metadata(provider_id, override)
        return ModelSpec(
            id=override,
            provider=provider_id,
            endpoint=endpoint,
            capabilities=("chat", "reasoning", "tool_use", "agentic"),
            context_window=metadata.get("context_window"),
            specialties=tuple(metadata.get("specialties", ())),
            quality_score=0.95,
            latency_score=0.7,
            cost_score=1.0,
            pricing_tier="free",
            enabled=True,
        )
    candidates = _free_nvidia_candidates(discovered)
    if not candidates:
        raise AssertionError("NVIDIA discovery returned no policy-authorized free model")
    return candidates[0]


@pytest.mark.live
def test_live_free_model_resilience_acceptance(tmp_path: Path):
    if os.getenv(LIVE_FLAG) != "1":
        pytest.skip("live free-model resilience gate is opt-in")

    gemini_key = _required("GEMINI_API_KEY")
    nvidia_key = _required("NVIDIA_API_KEY")
    nvidia_endpoint = os.getenv(
        "NVIDIA_API_ENDPOINT",
        "https://integrate.api.nvidia.com/v1",
    ).rstrip("/")
    gemini_model = os.getenv("GEMINI_MODEL_ID", "gemini-3.8-flash").strip()

    if not is_free_model("gemini", gemini_model):
        raise AssertionError(
            f"configured Gemini Director model is not authorized as free: {gemini_model}"
        )

    # 1. Discover the live NVIDIA catalog and retain only models authorized
    # by the official NVIDIA Free Endpoint catalog or known-good policy seeds.
    provider_registry = default_provider_registry(
        nvidia_api_key=nvidia_key,
    )
    discovery_store = ModelCandidateStore(tmp_path / "model-candidates.db")
    discovery_registry = ModelRegistry.from_specs(
        [],
        provider_registry=provider_registry,
    )
    discovery = ModelDiscoveryService(
        provider_registry,
        discovery_registry,
        discovery_store,
    )
    refreshed = discovery.refresh_all(
        provider_ids=("nvidia_api",),
        min_interval_seconds=0,
        force=True,
    )
    discovered = refreshed.get("nvidia_api", ())
    assert discovered
    free_candidates = _free_nvidia_candidates(discovered)
    assert len(free_candidates) >= 3, (
        "LIVE acceptance requires at least three currently discovered free NVIDIA "
        "reasoning candidates for ensemble/reviewer independence"
    )

    nvidia_primary = _override_or_free(
        "nvidia_api",
        "NVIDIA_MODEL_ID",
        discovered,
        endpoint=nvidia_endpoint,
    )
    selected_models = [nvidia_primary]
    for model in free_candidates:
        if model.id not in {item.id for item in selected_models}:
            selected_models.append(model)
        if len(selected_models) == 3:
            break
    assert len(selected_models) == 3
    assert all(item.pricing_tier == "free" for item in selected_models)

    # Discovery is intentionally non-authoritative. Promote this LIVE-test
    # snapshot only inside the ephemeral test registry after policy checks.
    selected_models = [
        ModelSpec(
            id=item.id,
            provider=item.provider,
            endpoint=nvidia_endpoint,
            capabilities=item.capabilities,
            context_window=item.context_window,
            enabled=True,
            specialties=item.specialties,
            quality_score=item.quality_score,
            latency_score=item.latency_score,
            cost_score=1.0,
            healthy=True,
            available=True,
            pricing_tier="free",
        )
        for item in selected_models
    ]

    model_registry = ModelRegistry.from_specs(
        selected_models,
        provider_registry=provider_registry,
    )
    health = ProviderHealthStore(
        tmp_path / "provider-health.db",
        failure_threshold=1,
        base_cooldown_seconds=2,
        max_cooldown_seconds=30,
    )
    router = ModelRouter(
        models=list(model_registry.models),
        nvidia_api_key=nvidia_key,
        provider_registry=provider_registry,
        model_registry=model_registry,
        health_store=health,
        cost_policy="free_only",
    )
    assert all(model.pricing_tier == "free" for model in router.available())
    assert {model.id for model in router.available()} == {
        model.id for model in selected_models
    }

    # 2. Real NVIDIA inference through multiple free models establishes durable health.
    for spec in selected_models:
        response = router.generate(
            InferenceRequest(
                messages=[{"role": "user", "content": "Return exactly LIVE_OK."}],
                model=spec.id,
                max_tokens=16,
            ),
            capability="reasoning",
        )
        assert response.content.strip()
        assert response.provider == "nvidia_api"
        assert response.model == spec.id
        snapshot = health.snapshot("nvidia_api", spec.id)
        assert snapshot.state == "healthy"
        assert snapshot.success_count >= 1

    # 3. Real Gemini Director followed by a controlled primary failure and a
    # real NVIDIA free-model replacement, preserving the same failover controller.
    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save(
        {
            "director": {
                "provider": "gemini",
                "model": gemini_model,
                "endpoint": "https://generativelanguage.googleapis.com/v1beta",
                "failover": {
                    "enabled": True,
                    "order": ["nvidia_api", "router"],
                },
            },
            "providers": {
                "gemini": {"api_key": gemini_key},
                "nvidia_api": {
                    "api_key": nvidia_key,
                    "base_url": nvidia_endpoint,
                },
            },
        }
    )
    primary = OneShotPrimary(
        GeminiDirectorProvider(
            api_key=gemini_key,
            model=gemini_model,
            endpoint="https://generativelanguage.googleapis.com/v1beta",
        )
    )
    controller = _build_director_provider_failover(
        settings_store=settings,
        router=router,
        active_provider=primary,
        db_path=tmp_path / "failover.db",
        health_store=health,
    )
    first = controller.generate(
        messages=[{"role": "user", "content": "Return PRIMARY_OK."}],
        model=gemini_model,
        role="director_planner",
    )
    second = controller.generate(
        messages=[{"role": "user", "content": "Return REPLACEMENT_OK."}],
        model=gemini_model,
        role="director_planner",
    )
    assert first.provider == "gemini"
    assert first.content.strip()
    assert second.provider == "nvidia_api"
    assert second.provider != "gemini"
    assert second.content.strip()
    assert controller.provider_id == "nvidia_api"

    evidence = DirectorProviderFailoverStore(str(tmp_path / "failover.db")).list_recent(limit=50)
    assert any(item["phase"] == "failure_detected" for item in evidence)
    assert any(
        item["phase"] == "succeeded" and item["to_provider"] == "nvidia_api"
        for item in evidence
    )

    # 4. Critical decisions use only free NVIDIA models through the same router.
    ensemble = ModelEnsemble(
        router,
        store=ModelEnsembleStore(tmp_path / "ensemble.db"),
        maximum_models=3,
        minimum_successful_models=2,
        concurrency=3,
    )
    ensemble_result = ensemble.run_critical_decision(
        InferenceRequest(
            messages=[
                {"role": "system", "content": "Reply exactly APPROVE."},
                {"role": "user", "content": "For this controlled acceptance, reply exactly APPROVE."},
            ],
            max_tokens=16,
        ),
        capability="reasoning",
        ensemble_size=3,
        resolve_disagreement=True,
    )
    successful_members = [item for item in ensemble_result.members if item.success]
    assert len(successful_members) >= 2
    assert ensemble_result.status in {"consensus", "resolved"}
    assert ensemble_result.decision
    assert all(item.model in {model.id for model in selected_models} for item in successful_members)

    # 5. DirectorRuntime adversarial review also runs through the free-only router.
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher", "researcher", "research",
                "independent evidence reviewer", frozenset(), (),
            ),
            "verifier": AgentSpec(
                "verifier", "verifier", "verification",
                "independent contradiction reviewer", frozenset(), (),
            ),
        },
        departments={
            "research": DepartmentSpec(
                "research", "research", ("researcher",), (), frozenset(),
            ),
            "verification": DepartmentSpec(
                "verification", "verification", ("verifier",), (), frozenset(),
            ),
        },
    )
    task_engine = TaskEngine(
        OrchestrationEngine(router),
        tmp_path / "review-workspace",
        organization=organization,
    )
    store = DurableTaskStore(tmp_path / "director.db")
    event_bus = DurableEventBus(store)
    durable = DurableDirectorService(
        planner=LivePlanner(),
        task_engine=task_engine,
        store=store,
        event_bus=event_bus,
    )
    durable.team_coordinator = TeamCoordinator(
        task_engine=task_engine,
        agent_selector=AgentSelector(organization),
        max_members=2,
    )
    runtime = DirectorRuntime(
        director=durable,
        store=store,
        event_bus=event_bus,
        worker_id="live-free-adversarial-review",
        event_consumer_id="live-free-adversarial-review-events",
        worker_concurrency=1,
        max_review_rounds=1,
        adversarial_review_enabled=True,
        adversarial_review_size=2,
    )
    submission = runtime.submit_mission(
        objective="Review this free-model acceptance mission for contradictions."
    )
    review = runtime._run_adversarial_review(
        mission_id=submission.mission_id,
        round_number=1,
        objective=submission.plan.objective,
    )
    successful_reviewers = [item for item in review.reviewers if item.error is None]
    assert len(successful_reviewers) >= 2
    assert all(item.model in {model.id for model in selected_models} for item in successful_reviewers)
    assert store.mission_status(submission.mission_id) in {
        TaskStatus.PENDING,
        TaskStatus.RUNNING,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
    }
    assert [
        event
        for event in store.events(submission.mission_id)
        if event.event_type == "director_adversarial_review"
    ]
