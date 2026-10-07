from pathlib import Path
from types import SimpleNamespace

from aurora.orchestration import factory


class FakeSettingsStore:
    def __init__(self, provider="nvidia_api", model="nvidia/dynamic-free-model"):
        self.provider = provider
        self.model = model

    def load(self):
        return {"director": {"provider": self.provider, "model": self.model}}


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
    monkeypatch.setattr(factory, "is_free_model", lambda provider, model: False)
    monkeypatch.setattr(factory, "ModelCandidateStore", lambda path: path)

    router = SimpleNamespace(provider_registry=object(), model_registry=object())
    settings = FakeSettingsStore()

    factory._activate_selected_discovered_models(router, settings, Path(tmp_path))

    assert [name for name, _ in FakeDiscovery.calls] == [
        "refresh_all",
        "activate_free_discovered",
    ]
    assert FakeDiscovery.calls[0][1]["provider_ids"] == ("nvidia_api",)
    assert FakeDiscovery.calls[1][1] == {"provider_id": "nvidia_api", "limit": 500}


def test_startup_does_not_refresh_static_free_model(monkeypatch, tmp_path):
    monkeypatch.setattr(factory, "is_free_model", lambda provider, model: True)
    monkeypatch.setattr(factory, "ModelDiscoveryService", lambda *args: (_ for _ in ()).throw(AssertionError("discovery should not run")))

    router = SimpleNamespace(provider_registry=object(), model_registry=object())
    settings = FakeSettingsStore(model="nvidia/nemotron-3-super-120b-a12b")

    factory._activate_selected_discovered_models(router, settings, Path(tmp_path))
