from pathlib import Path

from aurora.inference.discovery import DiscoveredModelCandidate, ModelCandidateStore, ModelDiscoveryService
from aurora.inference.models import ModelSpec
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
from aurora.inference.registry import ModelRegistry


def test_controlled_refresh_is_rate_limited_and_never_promotes(tmp_path: Path):
    registry = ProviderRegistry((
        ProviderSpec(
            "fake",
            "Fake",
            ("chat", "reasoning"),
            True,
            lambda model: object(),
            discovery_factory=lambda: object(),
        ),
    ))
    model_registry = ModelRegistry([], provider_registry=registry)
    candidates = ModelCandidateStore(tmp_path / "candidates.db")
    service = ModelDiscoveryService(registry, model_registry, candidates)

    staged = (
        DiscoveredModelCandidate(
            candidate_id="fake:model-a",
            provider_id="fake",
            model=ModelSpec(
                "model-a", "fake", "https://example.invalid", ("reasoning",)
            ),
            metadata={},
            discovered_at="2026-10-06T00:00:00+00:00",
        ),
    )
    calls = []
    service.discover_and_stage = lambda provider_id: (
        calls.append(provider_id) or staged
    )

    first = service.refresh_all(provider_ids=("fake",), min_interval_seconds=900)
    second = service.refresh_all(provider_ids=("fake",), min_interval_seconds=900)

    assert first["fake"] == staged
    assert second == {}
    assert calls == ["fake"]
    assert model_registry.ids == ()
