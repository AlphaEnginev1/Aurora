import pytest

from aurora.orchestration import factory


def test_default_director_provider_is_router_even_with_legacy_persisted_nvidia(monkeypatch):
    monkeypatch.delenv("AURORA_DIRECTOR_PROVIDER", raising=False)

    class Settings:
        def load(self):
            return {"director": {"provider": "nvidia_api", "model": "nvidia/nemotron-3.5-lightning-30b-a3b"}}

    assert factory._build_director_provider(Settings()) is None


def test_nvidia_can_be_selected_only_with_synced_official_catalog_receipt(
    monkeypatch, tmp_path
):
    from aurora.inference.discovery import DiscoveredModelCandidate, ModelCandidateStore, candidate_id
    from aurora.inference.models import ModelSpec

    model_id = "nvidia/current-catalog-model"
    active_model = ModelSpec(
        id=model_id,
        provider="nvidia_api",
        endpoint="https://integrate.api.nvidia.com/v1",
        capabilities=("chat", "reasoning"),
        enabled=True,
        healthy=True,
        available=True,
        pricing_tier="free",
    )

    class FakeModelRegistry:
        models = (active_model,)
        ids = (model_id,)

        def resolve(self, requested):
            if requested != model_id:
                raise KeyError(requested)
            return active_model

    class FakeProviderRegistry:
        ids = ("nvidia_api",)

    class FakeRouter:
        cost_policy = "free_only"
        model_registry = FakeModelRegistry()
        provider_registry = FakeProviderRegistry()

    class FakeBound:
        provider_id = "nvidia_api"
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", "nvidia_api")
    monkeypatch.setattr(factory, "BoundInferenceDirectorProvider", FakeBound)

    class Settings:
        path = tmp_path / "operator-settings.json"

        def load(self):
            return {
                "director": {
                    "provider": "nvidia_api",
                    "model": model_id,
                },
                "providers": {"nvidia_api": {"enabled": True}},
            }

        def credential(self, provider_id):
            return "secret" if provider_id == "nvidia_api" else ""

    candidates = ModelCandidateStore(tmp_path / "model-candidates.db")
    candidate = DiscoveredModelCandidate(
        candidate_id=candidate_id("nvidia_api", model_id),
        provider_id="nvidia_api",
        model=active_model,
        metadata={"free_endpoint_catalog": True},
        discovered_at="2026-10-18T00:00:00+00:00",
        state="synced",
    )
    candidates.upsert(candidate)
    candidates.set_state(candidate.candidate_id, "synced")

    provider = factory._build_director_provider(Settings(), router=FakeRouter())
    assert provider.provider_id == "nvidia_api"
    assert provider.kwargs["model"] == model_id


def test_non_nvidia_director_provider_falls_back_to_router(monkeypatch):
    for external in ("legacy_vendor_a", "legacy_vendor_b", "legacy_vendor_c"):
        monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", external)
        assert factory._build_director_provider() is None


def test_legacy_director_settings_migration_is_explicit():
    from aurora.operator_settings_migration import migrate_legacy_director_provider

    class Settings:
        def __init__(self):
            self.saved = []
        def load(self):
            return {
                "director": {
                    "provider": "nvidia_api",
                    "model": "nvidia/legacy-model",
                },
                "providers": {"nvidia_api": {"api_key": "secret"}},
            }
        def save(self, payload):
            self.saved.append(payload)
            return payload

    settings = Settings()
    assert migrate_legacy_director_provider(settings) is True
    assert settings.saved == [{"director": {"provider": "router", "model": "router"}}]


def test_router_director_settings_migration_is_noop():
    from aurora.operator_settings_migration import migrate_legacy_director_provider

    class Settings:
        def __init__(self):
            self.save_calls = 0
        def load(self):
            return {"director": {"provider": "router", "model": "router"}}
        def save(self, payload):
            self.save_calls += 1
            return payload

    settings = Settings()
    assert migrate_legacy_director_provider(settings) is False
    assert settings.save_calls == 0
