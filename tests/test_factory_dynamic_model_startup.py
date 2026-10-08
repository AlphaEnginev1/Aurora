from pathlib import Path
from types import SimpleNamespace

from aurora.orchestration import factory


class FakeSettingsStore:
    def __init__(self, provider="nvidia_api", model="nvidia/dynamic-free-model"):
        self.provider = provider
        self.model = model

    def load(self):
        return {"director": {"provider": self.provider, "model": self.model}}

    def credential(self, provider_id):
        return "test-key" if provider_id == "nvidia_api" else ""


class FakeDiscovery:
    calls = []

    def __init__(self, provider_registry, model_registry, candidate_store):
        self.candidate_store = candidate_store

    def refresh_all(self, **kwargs):
        self.calls.append(("refresh_all", kwargs))
        return {"nvidia_api": ()}

    def activate_free_discovered(self, **kwargs):
        self.calls.append(("activate_free_discovered", kwargs))
        return ()


def test_startup_activates_dynamic_nvidia_model_before_director_validation(monkeypatch, tmp_path):
    FakeDiscovery.calls = []
    monkeypatch.setattr(factory, "ModelDiscoveryService", FakeDiscovery)
    monkeypatch.setattr(factory, "ModelCandidateStore", lambda path: path)

    router = SimpleNamespace(
        provider_registry=SimpleNamespace(ids=("nvidia_api",)),
        model_registry=object(),
    )
    settings = FakeSettingsStore()

    factory._activate_selected_discovered_models(router, settings, Path(tmp_path))

    assert [name for name, _ in FakeDiscovery.calls] == [
        "refresh_all",
        "activate_free_discovered",
    ]
    assert FakeDiscovery.calls[0][1]["provider_ids"] == ("nvidia_api",)
    assert FakeDiscovery.calls[1][1] == {"provider_id": "nvidia_api", "limit": 500}


def test_startup_refreshes_dynamic_catalog_even_when_director_mode_is_router(monkeypatch, tmp_path):
    FakeDiscovery.calls = []
    monkeypatch.setattr(factory, "ModelDiscoveryService", FakeDiscovery)
    monkeypatch.setattr(factory, "ModelCandidateStore", lambda path: path)

    router = SimpleNamespace(
        provider_registry=SimpleNamespace(ids=("nvidia_api",)),
        model_registry=object(),
    )
    settings = FakeSettingsStore(provider="router")

    factory._activate_selected_discovered_models(router, settings, Path(tmp_path))

    assert FakeDiscovery.calls[0][0] == "refresh_all"
