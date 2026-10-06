from pathlib import Path

from aurora.inference.discovery import ModelCandidateStore, ModelDiscoveryService
from aurora.inference.models import ModelSpec
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec


class FakeDiscovery:
    provider_id = "fake"

    def __init__(self, calls):
        self.calls = calls

    def discover(self):
        self.calls.append(1)
        from aurora.inference.discovery import DiscoveredModelCandidate
        return (
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


def test_controlled_refresh_is_rate_limited_and_never_promotes(tmp_path: Path):
    calls = []
    registry = ProviderRegistry((
        ProviderSpec(
            "fake",
            "Fake",
            ("chat", "reasoning"),
            True,
            lambda model: object(),
            discovery_factory=lambda: FakeDiscovery(calls),
        ),
    ))
    model_registry = __import__("aurora.inference.registry", fromlist=["ModelRegistry"]).ModelRegistry(
        [], provider_registry=registry
    )
    candidates = ModelCandidateStore(tmp_path / "candidates.db")

    def factory(_provider_id):
        return FakeDiscovery(calls)

    registry = ProviderRegistry((
        ProviderSpec("fake", "Fake", ("chat", "reasoning"), True, lambda model: object(),
                     discovery_factory=factory),
    ))
    service = ModelDiscoveryService(registry, model_registry, candidates)

    first = service.refresh_all(provider_ids=("fake",), min_interval_seconds=900)
    second = service.refresh_all(provider_ids=("fake",), min_interval_seconds=900)

    assert first["fake"]
    assert second == {}
    assert len(calls) == 1
    assert candidates.get("fake:model-a").state == "discovered"
    assert model_registry.ids == ()
