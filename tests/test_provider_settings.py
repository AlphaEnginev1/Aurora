from pathlib import Path

from aurora.dashboard.provider_settings import ProviderSettingsService
from aurora.inference.models import ModelSpec
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
from aurora.inference.router import ModelRouter
from aurora.operator_settings import OperatorSettingsStore
from aurora.orchestration.director_provider import InferenceDirectorProvider


class Runtime:
    class Planner:
        def __init__(self, router):
            self.router = router
            self.provider = InferenceDirectorProvider(router)
    def __init__(self, router):
        class Director:
            def __init__(self, planner):
                self.planner = planner
        self.director = Director(self.Planner(router))
        self.conversation_service = None


def router(tmp_path: Path):
    model = ModelSpec(
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia_api",
        "https://integrate.api.nvidia.com/v1",
        capabilities=("reasoning", "chat"),
        enabled=False,
        pricing_tier="free",
    )
    registry = ProviderRegistry((
        ProviderSpec("nvidia_api", "NVIDIA", ("chat", "reasoning"), True, lambda spec: object()),
    ))
    return ModelRouter(
        models=[model],
        provider_registry=registry,
    )


def test_external_provider_setting_is_sanitized_to_nvidia_router(tmp_path: Path):
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    service = ProviderSettingsService(store, runtime)
    state = service.save_and_apply({"director": {"provider": "gemini", "model": "gemini-3.7-flash"}})
    assert state["director"]["provider"] == "router"
    assert state["providers"].keys() == {"nvidia_api"}

def test_nvidia_discovery_stages_candidates(tmp_path: Path):
    from types import SimpleNamespace

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {"nvidia_api": {"api_key": "secret"}},
    })

    candidate_a = SimpleNamespace(model=ModelSpec(
        "nvidia/model-a",
        "nvidia_api",
        "https://integrate.api.nvidia.com/v1",
        enabled=False,
        healthy=False,
        available=False,
        pricing_tier="free",
    ))
    candidate_b = SimpleNamespace(model=ModelSpec(
        "nvidia/model-b",
        "nvidia_api",
        "https://integrate.api.nvidia.com/v1",
        enabled=False,
        healthy=False,
        available=False,
        pricing_tier="free",
    ))

    class Discovery:
        def discover_and_stage(self, provider_id):
            assert provider_id == "nvidia_api"
            return (candidate_a, candidate_b)

    runtime.model_discovery = Discovery()
    service = ProviderSettingsService(store, runtime)

    result = service.test_provider("nvidia_api")

    assert result["ok"] is True
    assert result["models"] == ["nvidia/model-a", "nvidia/model-b"]
    assert result["staged_count"] == 2
    assert result["stage_state"] == "discovered"


def test_nvidia_selected_model_is_activated_without_editing_repo_policy(tmp_path: Path):
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {"nvidia_api": {"api_key": "secret"}},
    })
    service = ProviderSettingsService(store, runtime)

    state = service.save_and_apply({
        "director": {
            "provider": "nvidia_api",
            "model": "google/gemma-4-31b-it",
            "endpoint": "https://integrate.api.nvidia.com/v1",
        },
    })

    assert state["runtime"]["director_provider"] == "inference_router"
    model = runtime.director.planner.router.model_registry.resolve("google/gemma-4-31b-it")
    assert model.enabled is True
    assert model.provider == "nvidia_api"
    assert runtime.director.planner.provider.provider_id == "inference_router"


def test_local_nvidia_model_setting_can_be_restored_on_startup(tmp_path: Path):
    from aurora.orchestration.factory import _apply_local_model_settings
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "director": {
            "provider": "nvidia_api",
            "model": "poolside/laguna-xs-2.1",
            "endpoint": "https://integrate.api.nvidia.com/v1",
        },
        "providers": {"nvidia_api": {"api_key": "secret"}},
    })
    r = router(tmp_path)
    _apply_local_model_settings(r, store)
    restored = r.model_registry.resolve("poolside/laguna-xs-2.1")
    assert restored.enabled is True
    assert restored.provider == "nvidia_api"



def test_provider_settings_exposes_catalog_without_secret_values(tmp_path: Path):
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openai": {
                "api_key": "openai-private-value",
                "base_url": "https://api.openai.com/v1",
            },
        },
    })
    service = ProviderSettingsService(store, runtime)
    public = service.public()

    assert {item["provider_id"] for item in public["provider_catalog"]} >= {
        "nvidia_api", "openai", "anthropic", "openrouter"
    }
    assert public["providers"]["openai"]["api_key_present"] is True
    assert "openai-private-value" not in str(public)


def test_saved_openrouter_key_is_applied_to_runtime_registry(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openrouter": {
                "api_key": "openrouter-saved-key",
                "base_url": "https://openrouter-proxy.example/api/v1",
                "enabled": True,
            },
        },
    })
    service = ProviderSettingsService(store, runtime)

    result = service.save_and_apply({})
    live_router = runtime.director.planner.router
    registry = live_router.provider_registry
    assert "openrouter" in registry.ids
    registered_model = live_router.model_registry.resolve("openrouter/free")
    assert registered_model.endpoint == "https://openrouter-proxy.example/api/v1"
    provider = registry.create(registered_model)
    assert provider.api_key == "openrouter-saved-key"
    assert "openrouter-saved-key" not in str(result)


def test_openai_and_anthropic_saved_keys_register_only_configured_adapters(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openai": {
                "api_key": "openai-saved-key",
                "base_url": "https://api.openai.com/v1",
                "enabled": True,
            },
            "anthropic": {
                "api_key": "anthropic-saved-key",
                "base_url": "https://api.anthropic.com/v1",
                "enabled": True,
            },
        },
    })
    service = ProviderSettingsService(store, runtime)

    service.save_and_apply({})
    registry = runtime.director.planner.router.provider_registry
    assert {"openai", "anthropic", "nvidia_api"} <= set(registry.ids)
    openai = registry.create(ModelSpec("gpt-test", "openai", "https://api.openai.com/v1"))
    anthropic = registry.create(ModelSpec("claude-test", "anthropic", "https://api.anthropic.com/v1"))
    assert openai.api_key == "openai-saved-key"
    assert anthropic.api_key == "anthropic-saved-key"


def test_router_mode_can_save_provider_key_without_treating_router_as_model(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({"providers": {"nvidia_api": {"api_key": "nvidia-saved-key"}}})
    service = ProviderSettingsService(store, runtime)

    result = service.save_and_apply({"director": {"provider": "router", "model": "router"}})

    assert result["director"]["model"] == "router"
    assert result["providers"]["nvidia_api"]["api_key_present"] is True
