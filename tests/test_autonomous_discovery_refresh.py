from pathlib import Path

from aurora.inference.autonomous_discovery import AutonomousDiscoveryRefreshService
from aurora.inference.discovery import (
    DiscoveredModelCandidate,
    ModelCandidateStore,
    ModelDiscoveryService,
    candidate_id,
)
from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.provider_health import ProviderHealthStore
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec


def _registry() -> ProviderRegistry:
    return ProviderRegistry(
        (
            ProviderSpec(
                "fake",
                "Fake",
                ("chat", "reasoning"),
                True,
                lambda model: type("FakeProvider", (), {"provider_id": "fake"})(),
                discovery_factory=lambda: object(),
            ),
        )
    )


def _candidate(model_id: str) -> DiscoveredModelCandidate:
    model = ModelSpec(
        id=model_id,
        provider="fake",
        endpoint="https://example.invalid",
        capabilities=("chat", "reasoning"),
        pricing_tier="paid",
    )
    return DiscoveredModelCandidate(
        candidate_id=candidate_id("fake", model_id),
        provider_id="fake",
        model=model,
        metadata={},
        discovered_at="2026-10-08T00:00:00+00:00",
    )


def test_health_gates_discovered_activation_and_reserve_pool(tmp_path: Path):
    registry = _registry()
    model_registry = ModelRegistry([], provider_registry=registry)
    health = ProviderHealthStore(
        tmp_path / "health.db",
        failure_threshold=2,
        base_cooldown_seconds=60,
        max_cooldown_seconds=300,
    )
    store = ModelCandidateStore(tmp_path / "candidates.db")
    healthy = _candidate("healthy")
    blocked = _candidate("blocked")
    store.upsert(healthy)
    store.upsert(blocked)

    error = RuntimeError("provider unavailable")
    health.record_failure("fake", "blocked", error)
    health.record_failure("fake", "blocked", error)

    discovery = ModelDiscoveryService(registry, model_registry, store)
    activated = discovery.activate_eligible_discovered(
        provider_id="fake",
        cost_policy="all",
        health_store=health,
    )

    assert [item.id for item in activated] == ["healthy"]
    assert model_registry.ids == ("healthy",)

    router = ModelRouter(
        models=list(model_registry.models),
        provider_registry=registry,
        model_registry=model_registry,
        health_store=health,
        cost_policy="all",
    )
    request = InferenceRequest(messages=[{"role": "user", "content": "hello"}])
    pool = AutonomousDiscoveryRefreshService(
        discovery=discovery,
        router=router,
        health_store=health,
        provider_ids=("fake",),
        interval_seconds=900,
    )
    reserve = pool.build_reserve_pool(request, capability="reasoning")

    assert [item.model.id for item in reserve] == ["healthy"]


def test_autonomous_refresh_is_rate_limited_and_updates_registry(tmp_path: Path):
    registry = _registry()
    model_registry = ModelRegistry([], provider_registry=registry)
    health = ProviderHealthStore(tmp_path / "health.db")
    store = ModelCandidateStore(tmp_path / "candidates.db")
    discovery = ModelDiscoveryService(registry, model_registry, store)
    staged = _candidate("new-model")
    calls = []

    def refresh_all(**kwargs):
        calls.append(kwargs)
        store.upsert(staged)
        return {"fake": (staged,)}

    discovery.refresh_all = refresh_all
    router = ModelRouter(
        models=[],
        provider_registry=registry,
        model_registry=model_registry,
        health_store=health,
        cost_policy="all",
    )
    service = AutonomousDiscoveryRefreshService(
        discovery=discovery,
        router=router,
        health_store=health,
        provider_ids=("fake",),
        interval_seconds=900,
    )

    first = service.refresh_if_due()
    second = service.refresh_if_due()

    assert first.refreshed is True
    assert first.activated_count == 1
    assert second.refreshed is False
    assert calls and len(calls) == 1
    assert model_registry.ids == ("new-model",)
