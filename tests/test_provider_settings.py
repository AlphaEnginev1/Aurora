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
        "nvidia/base",
        "nvidia_api",
        "https://integrate.api.nvidia.com/v1",
        capabilities=("reasoning", "chat"),
        enabled=False,
    )
    registry = ProviderRegistry((
        ProviderSpec("nvidia_api", "NVIDIA", ("chat", "reasoning"), True, lambda spec: object()),
    ))
    return ModelRouter(
        models=[model],
        provider_registry=registry,
    )


def test_gemini_setting_switches_runtime_provider(tmp_path: Path):
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    service = ProviderSettingsService(store, runtime)

    state = service.save_and_apply({
        "director": {
            "provider": "gemini",
            "model": "gemini-3.7-flash",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta",
        },
        "providers": {"gemini": {"api_key": "secret"}},
    })

    assert state["runtime"]["director_provider"] == "gemini"
    assert runtime.director.planner.provider.provider_id == "gemini"
    assert runtime.director.planner.router.nvidia_api_key is None


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
            "model": "nvidia/new-model",
            "endpoint": "https://integrate.api.nvidia.com/v1",
        },
    })

    assert state["runtime"]["director_provider"] == "inference_router"
    model = runtime.director.planner.router.model_registry.resolve("nvidia/new-model")
    assert model.enabled is True
    assert model.provider == "nvidia_api"
    assert runtime.director.planner.provider.provider_id == "inference_router"


def test_local_nvidia_model_setting_can_be_restored_on_startup(tmp_path: Path):
    from aurora.orchestration.factory import _apply_local_model_settings
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "director": {
            "provider": "nvidia_api",
            "model": "nvidia/persisted-model",
            "endpoint": "https://integrate.api.nvidia.com/v1",
        },
        "providers": {"nvidia_api": {"api_key": "secret"}},
    })
    r = router(tmp_path)
    _apply_local_model_settings(r, store)
    restored = r.model_registry.resolve("nvidia/persisted-model")
    assert restored.enabled is True
    assert restored.provider == "nvidia_api"
