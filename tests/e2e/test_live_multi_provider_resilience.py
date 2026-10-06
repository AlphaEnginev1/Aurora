"""LIVE acceptance for Aurora's current multi-provider resilience layer.

The gate is opt-in and fails closed. It exercises real NVIDIA, OpenAI and
Anthropic providers through the existing Aurora routing, discovery, failover,
critical-ensemble and adversarial-review components.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import pytest

from aurora.inference.discovery import ModelCandidateStore, ModelDiscoveryService
from aurora.inference.ensemble import ModelEnsemble, ModelEnsembleStore
from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.provider_health import ProviderHealthStore
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter
from aurora.operator_settings import OperatorSettingsStore
from aurora.organization import AgentSpec, AgentSelector, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director import DirectorPlan
from aurora.orchestration.director_provider_failover import DirectorProviderFailoverStore
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import OrchestrationEngine
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.factory import _build_director_provider_failover
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.team_runtime import TeamCoordinator
from aurora.inference.providers.openai_api import OpenAIAPIProvider

LIVE_FLAG = "AURORA_MULTI_PROVIDER_LIVE"

def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise AssertionError(f"live multi-provider gate requires {name}")
    return value

@dataclass
class OneShotPrimary:
    provider: object
    def __post_init__(self):
        self.calls = 0
        self.provider_id = str(getattr(self.provider, "provider_id", ""))
    def generate(self, *, messages, model, role, tools=()):
        self.calls += 1
        if self.calls == 1:
            return self.provider.generate(messages=messages, model=model, role=role, tools=tools)
        error = RuntimeError("controlled live primary provider outage")
        error.failoverable = True
        raise error

class LivePlanner:
    provider = None
    def plan(self, *, objective, context=None, model=None):
        return DirectorPlan(
            objective=objective,
            tasks=(DelegatedTask("seed-review", "researcher",
                                 "Keep one concise evidence item for review."),),
            model=model or "live/planner",
        )

def _router(tmp_path: Path, *, nvidia_model: str, openai_model: str,
            anthropic_model: str, nvidia_endpoint: str,
            nvidia_key: str, openai_key: str, anthropic_key: str):
    registry = default_provider_registry(
        nvidia_api_key=nvidia_key, openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
    )
    models = [
        ModelSpec(nvidia_model, "nvidia_api", nvidia_endpoint, ("chat", "reasoning"), 131072, True),
        ModelSpec(openai_model, "openai", "https://api.openai.com/v1", ("chat", "reasoning"), 200000, True),
        ModelSpec(anthropic_model, "anthropic", "https://api.anthropic.com/v1", ("chat", "reasoning"), 200000, True),
    ]
    model_registry = ModelRegistry.from_specs(models, provider_registry=registry)
    health = ProviderHealthStore(
        tmp_path / "provider-health.db",
        failure_threshold=1,
        base_cooldown_seconds=2,
        max_cooldown_seconds=30,
    )
    return (
        ModelRouter(
            models=list(model_registry.models),
            nvidia_api_key=nvidia_key,
            provider_registry=registry,
            model_registry=model_registry,
            health_store=health,
        ),
        registry,
        health,
    )

@pytest.mark.live
def test_live_multi_provider_resilience_acceptance(tmp_path: Path):
    if os.getenv(LIVE_FLAG) != "1":
        pytest.skip("live multi-provider resilience gate is opt-in")

    nvidia_key = _required("NVIDIA_API_KEY")
    nvidia_model = _required("NVIDIA_MODEL_ID")
    openai_key = _required("OPENAI_API_KEY")
    openai_model = _required("OPENAI_MODEL_ID")
    anthropic_key = _required("ANTHROPIC_API_KEY")
    anthropic_model = _required("ANTHROPIC_MODEL_ID")
    nvidia_endpoint = os.getenv(
        "NVIDIA_API_ENDPOINT",
        "https://integrate.api.nvidia.com/v1",
    ).rstrip("/")

    router, provider_registry, health = _router(
        tmp_path,
        nvidia_model=nvidia_model, openai_model=openai_model,
        anthropic_model=anthropic_model, nvidia_endpoint=nvidia_endpoint,
        nvidia_key=nvidia_key, openai_key=openai_key, anthropic_key=anthropic_key,
    )

    # 1. Real inference through all three provider adapters + health feedback.
    for model_id in (nvidia_model, openai_model, anthropic_model):
        response = router.generate(
            InferenceRequest(
                messages=[{"role": "user", "content": "Return exactly LIVE_OK."}],
                model=model_id, max_tokens=16,
            ),
            capability="reasoning",
        )
        assert response.content.strip()
        assert response.model == model_id
        assert response.provider in {"nvidia_api", "openai", "anthropic"}
        assert health.snapshot(response.provider, model_id).state == "healthy"

    # 2. Live discovery and durable, non-promoting staging.
    candidates = ModelCandidateStore(tmp_path / "model-candidates.db")
    discovery = ModelDiscoveryService(provider_registry, router.model_registry, candidates)
    refreshed = discovery.refresh_all(
        provider_ids=("nvidia_api", "openai", "anthropic"),
        min_interval_seconds=0,
        force=True,
    )
    for provider_id in ("nvidia_api", "openai", "anthropic"):
        assert refreshed.get(provider_id)
        assert candidates.list(provider_id=provider_id, state="discovered", limit=5)

    # 3. Dynamic reserve pool + real cross-provider failover.
    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save({
        "director": {
            "provider": "openai",
            "model": openai_model,
            "endpoint": "https://api.openai.com/v1",
            "failover": {"enabled": True, "order": ["anthropic", "nvidia_api", "router"]},
        },
        "providers": {
            "openai": {"api_key": openai_key, "base_url": "https://api.openai.com/v1"},
            "anthropic": {"api_key": anthropic_key, "base_url": "https://api.anthropic.com/v1"},
            "nvidia_api": {"api_key": nvidia_key, "base_url": nvidia_endpoint},
        },
    })
    primary = OneShotPrimary(
        OpenAIAPIProvider(
            ModelSpec(openai_model, "openai", "https://api.openai.com/v1", ("chat", "reasoning")),
            api_key=openai_key,
            timeout=90,
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
        model=openai_model, role="director_planner",
    )
    second = controller.generate(
        messages=[{"role": "user", "content": "Return REPLACEMENT_OK."}],
        model=openai_model, role="director_planner",
    )
    assert first.provider == "openai" and first.content.strip()
    assert second.provider in {"anthropic", "nvidia_api"} and second.content.strip()
    assert controller.provider_id == second.provider
    evidence = DirectorProviderFailoverStore(str(tmp_path / "failover.db")).list_recent(limit=50)
    assert any(item["phase"] == "failure_detected" for item in evidence)
    assert any(item["phase"] == "succeeded" and item["to_provider"] == second.provider for item in evidence)

    # 4. Critical-decision ensemble through the existing ModelRouter.
    ensemble = ModelEnsemble(
        router,
        store=ModelEnsembleStore(tmp_path / "ensemble.db"),
        maximum_models=3, minimum_successful_models=2, concurrency=3,
    )
    result = ensemble.run_critical_decision(
        InferenceRequest(messages=[
            {"role": "system", "content": "Reply exactly APPROVE."},
            {"role": "user", "content": "For the controlled acceptance, reply exactly APPROVE."},
        ], max_tokens=16),
        capability="reasoning", ensemble_size=3, resolve_disagreement=True,
    )
    successful = [item for item in result.members if item.success]
    assert len(successful) >= 2
    assert result.status in {"consensus", "resolved"}
    assert result.decision

    # 5. DirectorRuntime adversarial review with two governed specialists.
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec("researcher", "researcher", "research",
                                    "independent evidence reviewer", frozenset(), ()),
            "verifier": AgentSpec("verifier", "verifier", "verification",
                                  "independent contradiction reviewer", frozenset(), ()),
        },
        departments={
            "research": DepartmentSpec("research", "research", ("researcher",), (), frozenset()),
            "verification": DepartmentSpec("verification", "verification", ("verifier",), (), frozenset()),
        },
    )
    task_engine = TaskEngine(
        OrchestrationEngine(router),
        tmp_path / "review-workspace",
        organization=organization,
    )
    store = DurableTaskStore(tmp_path / "director.db")
    bus = DurableEventBus(store)
    durable = DurableDirectorService(
        planner=LivePlanner(), task_engine=task_engine,
        store=store, event_bus=bus,
    )
    durable.team_coordinator = TeamCoordinator(
        task_engine=task_engine,
        agent_selector=AgentSelector(organization),
        max_members=2,
    )
    runtime = DirectorRuntime(
        director=durable, store=store, event_bus=bus,
        worker_id="live-adversarial-review",
        event_consumer_id="live-adversarial-review-events",
        worker_concurrency=1, max_review_rounds=1,
        adversarial_review_enabled=True, adversarial_review_size=2,
    )
    submission = runtime.submit_mission(
        objective="Review this acceptance mission for missing evidence and contradictions.",
    )
    review = runtime._run_adversarial_review(
        mission_id=submission.mission_id,
        round_number=1,
        objective=submission.plan.objective,
    )
    successful_reviewers = [item for item in review.reviewers if item.error is None]
    assert len(successful_reviewers) >= 2
    assert review.status in {"accepted", "challenged", "blocked"}
    assert all(item.model for item in successful_reviewers)
    assert store.mission_status(submission.mission_id) in {
        TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.SUCCEEDED,
        TaskStatus.FAILED, TaskStatus.BLOCKED,
    }
    assert [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_adversarial_review"
    ]
