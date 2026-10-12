from __future__ import annotations

from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.registry import ModelRegistry
from aurora.orchestration.factory import _build_director_provider, _register_openrouter_free_model


def test_openrouter_model_is_added_only_when_key_is_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    registry = default_provider_registry(nvidia_api_key="nvidia-test")
    model_registry = ModelRegistry.from_file(
        "config/inference-policy.json",
        provider_registry=registry,
    )
    before = model_registry.ids
    _register_openrouter_free_model(model_registry, "")
    assert model_registry.ids == before
    assert "openrouter/free" not in model_registry.ids


def test_openrouter_free_model_is_registered_with_free_policy(tmp_path):
    registry = default_provider_registry(
        nvidia_api_key="nvidia-test",
        openrouter_api_key="openrouter-test",
        include_openrouter=True,
    )
    model_registry = ModelRegistry.from_file(
        "config/inference-policy.json",
        provider_registry=registry,
    )
    _register_openrouter_free_model(model_registry, "openrouter-test")
    model = model_registry.resolve("openrouter/free")
    assert model.provider == "openrouter"
    assert model.endpoint == "https://openrouter.ai/api/v1"
    assert model.enabled is True
    assert model.pricing_tier == "free"


def test_explicit_openrouter_director_mode_requires_key_and_registered_model(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test")
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "openrouter/free")
    model = SimpleNamespace(id="openrouter/free", provider="openrouter", pricing_tier="free")
    router = SimpleNamespace(
        cost_policy="free_only",
        model_registry=SimpleNamespace(ids=("openrouter/free",), models=(model,)),
    )
    provider = _build_director_provider(router=router)
    assert provider is not None
    assert provider.provider_id == "openrouter"
