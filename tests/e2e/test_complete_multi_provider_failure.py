from __future__ import annotations

from pathlib import Path

from aurora.inference.discovery import (
    DiscoveredModelCandidate,
    ModelCandidateStore,
    ModelDiscoveryService,
    candidate_id,
)
from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.provider_health import ProviderHealthStore
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter
from aurora.inference.autonomous_discovery import AutonomousDiscoveryRefreshService
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.director_provider_failover import (
    DirectorProviderCandidate,
    DirectorProviderFailoverController,
    DirectorProviderFailoverStore,
)


class FakeProvider:
    def __init__(self, provider_id: str, outcomes: list[object]) -> None:
        self.provider_id = provider_id
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, *, messages, model, role, tools=()):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return DirectorModelResponse(
            content=str(outcome),
            model=model or f"{self.provider_id}-model",
            provider=self.provider_id,
            request_id=f"{self.provider_id}-request-{self.calls}",
            execution_id=f"{self.provider_id}-execution-{self.calls}",
            tool_calls=(),
        )


def _provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        (
            ProviderSpec(
                "gemini",
                "Gemini",
                ("chat", "reasoning"),
                True,
                lambda model: object(),
            ),
            ProviderSpec(
                "nvidia_api",
                "NVIDIA",
                ("chat", "reasoning"),
                True,
                lambda model: object(),
            ),
        )
    )


def _candidate(provider: str, model_id: str, *, quality: float) -> DiscoveredModelCandidate:
    model = ModelSpec(
        id=model_id,
        provider=provider,
        endpoint="https://example.invalid",
        capabilities=("chat", "reasoning"),
        quality_score=quality,
        latency_score=0.8,
        cost_score=1.0,
        pricing_tier="free",
    )
    return DiscoveredModelCandidate(
        candidate_id=candidate_id(provider, model_id),
        provider_id=provider,
        model=model,
        metadata={"free_endpoint_catalog": True},
        discovered_at="2026-10-08T00:00:00+00:00",
    )


def test_complete_multi_provider_failure_uses_discovery_health_reserve_and_continuity(
    tmp_path: Path,
):
    registry = _provider_registry()
    model_registry = ModelRegistry([], provider_registry=registry)
    health = ProviderHealthStore(
        tmp_path / "health.db",
        failure_threshold=1,
        base_cooldown_seconds=60,
        max_cooldown_seconds=60,
    )
    store = ModelCandidateStore(tmp_path / "candidates.db")
    discovery = ModelDiscoveryService(registry, model_registry, store)

    primary = _candidate("gemini", "gemini-primary", quality=1.0)
    reserve = _candidate("nvidia_api", "nvidia-reserve", quality=0.9)
    blocked = _candidate("nvidia_api", "nvidia-blocked", quality=0.95)
    for item in (primary, reserve, blocked):
        store.upsert(item)

    # The failed reserve model is unavailable before candidate materialization.
    health.record_failure("nvidia_api", "nvidia-blocked", RuntimeError("forced health failure"))

    activated = discovery.activate_eligible_discovered(
        provider_id="nvidia_api",
        cost_policy="free_only",
        health_store=health,
    )
    assert [model.id for model in activated] == ["nvidia-reserve"]

    router = ModelRouter(
        models=list(model_registry.models),
        provider_registry=registry,
        model_registry=model_registry,
        health_store=health,
        cost_policy="free_only",
    )
    request = InferenceRequest(
        messages=[{"role": "user", "content": "continue the same mission"}],
        metadata={"role": "director_planner"},
    )
    refresh = AutonomousDiscoveryRefreshService(
        discovery=discovery,
        router=router,
        health_store=health,
        provider_ids=("nvidia_api",),
        interval_seconds=900,
    )
    reserve_pool = refresh.build_reserve_pool(
        request,
        capability="reasoning",
        exclude=(("gemini", "gemini-primary"),),
    )
    assert [item.model.id for item in reserve_pool] == ["nvidia-reserve"]

    gemini = FakeProvider("gemini", [RuntimeError("forced primary failure")])
    nvidia = FakeProvider("nvidia_api", ["same mission continued"])
    candidates = tuple(
        DirectorProviderCandidate(
            provider_id=item.model.provider,
            model=item.model.id,
            endpoint=item.model.endpoint,
            factory=lambda p=nvidia: p,
        )
        for item in reserve_pool
    )
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=candidates,
        store=DirectorProviderFailoverStore(str(tmp_path / "failover.db")),
        activate=lambda _candidate, _provider: None,
        health_store=health,
        context_provider=lambda: {
            "mission_id": "mission-e2e-1",
            "session_id": "session-e2e-1",
        },
    )

    result = controller.generate(
        messages=request.messages,
        model=None,
        role="director_planner",
    )

    assert result.provider == "nvidia_api"
    assert result.content == "same mission continued"
    assert gemini.calls == 1
    assert nvidia.calls == 1
    assert controller.provider_id == "nvidia_api"
    assert health.snapshot("nvidia_api", "nvidia-blocked").state == "cooldown"
    transitions = controller._store.list_recent(limit=20)
    assert any(row["phase"] == "succeeded" and row["to_provider"] == "nvidia_api" for row in transitions)
    assert any(row["phase"] == "failure_detected" and row["from_provider"] == "gemini" for row in transitions)
