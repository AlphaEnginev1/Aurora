import pytest

from aurora.orchestration import factory


def test_default_director_provider_is_nvidia_router(monkeypatch):
    monkeypatch.delenv("AURORA_DIRECTOR_PROVIDER", raising=False)
    assert factory._build_director_provider() is None


def test_nvidia_can_be_selected_as_director_provider(monkeypatch):
    class FakeRouter:
        cost_policy = "free_only"
        model_registry = type("MR", (), {"models": ()})()

    class FakeBound:
        provider_id = "nvidia_api"
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", "nvidia_api")
    monkeypatch.setattr(factory, "BoundInferenceDirectorProvider", FakeBound)

    class Settings:
        def load(self):
            return {"director": {"provider": "nvidia_api", "model": "nvidia/nemotron-3-ultra-550b-a55b"}}
        def credential(self, provider_id):
            return "secret" if provider_id == "nvidia_api" else ""

    provider = factory._build_director_provider(Settings(), router=FakeRouter())
    assert provider.provider_id == "nvidia_api"
    assert provider.kwargs["model"] == "nvidia/nemotron-3-ultra-550b-a55b"


def test_non_nvidia_director_provider_falls_back_to_router(monkeypatch):
    for external in ("legacy_vendor_a", "legacy_vendor_b", "legacy_vendor_c"):
        monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", external)
        assert factory._build_director_provider() is None
