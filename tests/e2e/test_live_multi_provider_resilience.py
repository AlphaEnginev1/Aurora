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
from aurora.inference.providers.openai_api import OpenAIAPIProvider
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter
from aurora.operator_settings import OperatorSettingsStore
from aurora.organization import (
    AgentSelector,
    AgentSpec,
    DepartmentSpec,
    OrganizationRegistry,
)
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


def _choose_candidate(
    provider_id: str,
    env_name: str,
    discovered,
    *,
    endpoint: str,
    exclude_markers: tuple[str, ...] = (),
) -> ModelSpec:
    override = os.getenv(env_name, "").strip()
    if override:
        return ModelSpec(
            override,
            provider_id,
            endpoint,
            ("chat", "reasoning"),
            enabled=True,
        )

    for item in sorted(
        discovered,
        key=lambda candidate: (
            -float(candidate.model.quality_score),
            candidate.model.id,
        ),
    ):
        model_id = item.model.id.lower()
        if "reasoning" not in item.model.capabilities:
            continue
        if any(marker in model_id for marker in exclude_markers):
            continue
        return item.model
    raise AssertionError(
        f"no executable reasoning candidate discovered for {provider_id}"
    )


@pytest.mark.live
def test_live_multi_provider_resilience_acceptance(tmp_path: Path):
    if os.getenv(LIVE_FLAG) != "1":
        pytest.skip("live multi-provider resilience gate is opt-in")

    nvidia_key = _required("NVIDIA_API_KEY")
    openai_key = _required("OPENAI_API_KEY")
    anthropic_key = _required("ANTHROPIC_API_KEY")
    nvidia_endpoint = os.getenv(
        "NVIDIA_API_ENDPOINT",
        "https://integrate.api.nvidia.com/v1",
    ).rstrip("/")

    # 1. Discover the current provider catalogs first. Model IDs are optional
    # workflow overrides; otherwise Aurora selects a currently served candidate.
    provider_registry = default_provider_registry(
        nvidia_api_key=nvidia_key,
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
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
        provider_ids=("nvidia_api", "openai", "anthropic"),
        min_interval_seconds=0,
        force=True,
    )
    for provider_id in ("nvidia_api", "openai", "anthropic"):
        assert refreshed.get(provider_id)
        assert discovery_store.list(
            provider_id=provider_id,
            state="discovered",
            limit=5,
        )

    nvidia_spec = _choose_candidate(
        "nvidia_api",
        "NVIDIA_MODEL_ID",
        refreshed["nvidia_api"],
        endpoint=nvidia_endpoint,
        exclude_markers=("embedding", "embed", "guard", "safety", "rerank", "ranker"),
    )
    openai_spec = _choose_candidate(
        "openai",
        "OPENAI_MODEL_ID",
        refreshed["openai"],
        endpoint="https://api.openai.com/v1",
        exclude_markers=("embedding", "moderation", "whisper", "tts", "dall-e", "search"),
    )
    anthropic_spec = _choose_candidate(
        "anthropic",
        "ANTHROPIC_MODEL_ID",
        refreshed["anthropic"],
        endpoint="https://api.anthropic.com/v1",
    )

    model_registry = ModelRegistry.from_specs(
        [nvidia_spec, openai_spec, anthropic_spec],
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
    )

    # 2. Real provider adapters plus durable health feedback.
    for spec in (nvidia_spec, openai_spec, anthropic_spec):
        response = router.generate(
            InferenceRequest(
                messages=[
                    {
                        "role": "user",
                        "content": "Return exactly LIVE_OK.",
                    }
                ],
                model=spec.id,
                max_tokens=16,
            ),
            capability="reasoning",
        )
        assert response.content.strip()
        assert response.provider == spec.provider
        assert response.model == spec.id
        snapshot = health.snapshot(spec.provider, spec.id)
        assert snapshot.state == "healthy"
        assert snapshot.success_count >= 1

    # 3. Dynamic reserve pool plus a real cross-provider replacement.
    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save(
        {
            "director": {
                "provider": "openai",
                "model": openai_spec.id,
                "endpoint": "https://api.openai.com/v1",
                "failover": {
                    "enabled": True,
                    "order": ["anthropic", "nvidia_api", "router"],
                },
            },
            "providers": {
                "openai": {
                    "api_key": openai_key,
                    "base_url": "https://api.openai.com/v1",
                },
                "anthropic": {
                    "api_key": anthropic_key,
                    "base_url": "https://api.anthropic.com/v1",
                },
                "nvidia_api": {
                    "api_key": nvidia_key,
                    "base_url": nvidia_endpoint,
                },
            },
        }
    )
    primary = OneShotPrimary(
        OpenAIAPIProvider(
            openai_spec,
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
        model=openai_spec.id,
        role="director_planner",
    )
    second = controller.generate(
        messages=[{"role": "user", "content": "Return REPLACEMENT_OK."}],
        model=openai_spec.id,
        role="director_planner",
    )
    assert first.provider == "openai"
    assert first.content.strip()
    assert second.provider in {"anthropic", "nvidia_api"}
    assert second.provider != "openai"
    assert second.content.strip()
    assert controller.provider_id == second.provider

    evidence = DirectorProviderFailoverStore(
        str(tmp_path / "failover.db")
    ).list_recent(limit=50)
    assert any(item["phase"] == "failure_detected" for item in evidence)
    assert any(
        item["phase"] == "succeeded"
        and item["to_provider"] == second.provider
        for item in evidence
    )

    # 4. Critical-decision ensemble through the same ModelRouter.
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
                {
                    "role": "user",
                    "content": "For this controlled acceptance, reply exactly APPROVE.",
                },
            ],
            max_tokens=16,
        ),
        capability="reasoning",
        ensemble_size=3,
        resolve_disagreement=True,
    )
    successful_members = [
        item for item in ensemble_result.members if item.success
    ]
    assert len(successful_members) >= 2
    assert ensemble_result.status in {"consensus", "resolved"}
    assert ensemble_result.decision

    # 5. DirectorRuntime adversarial review with two governed specialists.
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher",
                "researcher",
                "research",
                "independent evidence reviewer",
                frozenset(),
                (),
            ),
            "verifier": AgentSpec(
                "verifier",
                "verifier",
                "verification",
                "independent contradiction reviewer",
                frozenset(),
                (),
            ),
        },
        departments={
            "research": DepartmentSpec(
                "research",
                "research",
                ("researcher",),
                (),
                frozenset(),
            ),
            "verification": DepartmentSpec(
                "verification",
                "verification",
                ("verifier",),
                (),
                frozenset(),
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
        worker_id="live-adversarial-review",
        event_consumer_id="live-adversarial-review-events",
        worker_concurrency=1,
        max_review_rounds=1,
        adversarial_review_enabled=True,
        adversarial_review_size=2,
    )
    submission = runtime.submit_mission(
        objective=(
            "Review this acceptance mission for missing evidence "
            "and contradictions."
        ),
    )
    review = runtime._run_adversarial_review(
        mission_id=submission.mission_id,
        round_number=1,
        objective=submission.plan.objective,
    )
    successful_reviewers = [
        item for item in review.reviewers if item.error is None
    ]
    assert len(successful_reviewers) >= 2
    assert review.status in {"accepted", "challenged", "blocked"}
    assert all(item.model for item in successful_reviewers)
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
