import json
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
                "base_url": "https://openai-proxy.example/v1",
                "enabled": True,
            },
            "anthropic": {
                "api_key": "anthropic-saved-key",
                "base_url": "https://anthropic-proxy.example/v1",
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
    assert openai.spec.endpoint == "https://openai-proxy.example/v1"
    assert anthropic.spec.endpoint == "https://anthropic-proxy.example/v1"


def test_router_mode_can_save_provider_key_without_treating_router_as_model(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({"providers": {"nvidia_api": {"api_key": "nvidia-saved-key"}}})
    service = ProviderSettingsService(store, runtime)

    result = service.save_and_apply({"director": {"provider": "router", "model": "router"}})

    assert result["director"]["model"] == "router"
    assert result["providers"]["nvidia_api"]["api_key_present"] is True



def test_settings_rebinds_live_discovery_to_new_provider_and_model_registries(tmp_path: Path):
    from types import SimpleNamespace

    runtime = Runtime(router(tmp_path))
    old_router = runtime.director.planner.router
    runtime.model_discovery = SimpleNamespace(
        provider_registry=old_router.provider_registry,
        model_registry=old_router.model_registry,
    )
    runtime.autonomous_discovery_refresh = SimpleNamespace(
        discovery=runtime.model_discovery,
        router=old_router,
        health_store=None,
    )
    runtime.dashboard_gateway = SimpleNamespace(
        provider_registry=old_router.provider_registry,
        model_registry=old_router.model_registry,
        model_router=old_router,
    )

    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "nvidia_api": {
                "api_key": "nvidia-key-added-in-settings",
                "enabled": True,
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
        },
    })
    service = ProviderSettingsService(store, runtime)

    service.save_and_apply({})

    live_router = runtime.director.planner.router
    assert runtime.model_discovery.provider_registry is live_router.provider_registry
    assert runtime.model_discovery.model_registry is live_router.model_registry
    assert runtime.autonomous_discovery_refresh.discovery is runtime.model_discovery
    assert runtime.autonomous_discovery_refresh.router is live_router
    assert runtime.autonomous_discovery_refresh.health_store is live_router.health_store
    assert runtime.dashboard_gateway.provider_registry is live_router.provider_registry
    assert runtime.dashboard_gateway.model_registry is live_router.model_registry
    assert runtime.dashboard_gateway.model_router is live_router


def test_openai_connection_probe_verifies_auth_and_redacts_the_secret(tmp_path: Path, monkeypatch):
    from aurora.dashboard import provider_settings as provider_settings_module
    from aurora.dashboard.provider_settings import ProviderSettingsService
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openai": {
                "api_key": "openai-credential-secret",
                "base_url": "https://api.openai.com/v1",
                "enabled": True,
            },
        },
    })
    captured = {}

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({"object": "list", "data": [{"id": "gpt-test"}]}).encode()

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr(provider_settings_module, "_urlopen", fake_urlopen)
    result = ProviderSettingsService(store, runtime).test_provider("openai")

    assert captured["url"] == "https://api.openai.com/v1/models"
    assert captured["authorization"] == "Bearer openai-credential-secret"
    assert result["ok"] is True
    assert result["connection_test"] == "authenticated_model_list"
    assert result["inference_verified"] is False
    assert result["models"] == ["gpt-test"]
    assert "openai-credential-secret" not in str(result)


def test_anthropic_connection_probe_uses_anthropic_auth_headers(tmp_path: Path, monkeypatch):
    from aurora.dashboard import provider_settings as provider_settings_module
    from aurora.dashboard.provider_settings import ProviderSettingsService
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "anthropic": {
                "api_key": "anthropic-credential-secret",
                "base_url": "https://api.anthropic.com/v1",
                "enabled": True,
            },
        },
    })
    captured = {}

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({"data": [{"id": "claude-test"}]}).encode()

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["api_key"] = request.get_header("X-api-key")
        captured["version"] = request.get_header("Anthropic-version")
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr(provider_settings_module, "_urlopen", fake_urlopen)
    result = ProviderSettingsService(store, runtime).test_provider("anthropic")

    assert captured["url"] == "https://api.anthropic.com/v1/models?limit=1"
    assert captured["api_key"] == "anthropic-credential-secret"
    assert captured["version"] == "2023-06-01"
    assert captured["authorization"] is None
    assert result["models"] == ["claude-test"]
    assert "anthropic-credential-secret" not in str(result)


def test_catalog_only_provider_connection_test_fails_closed(tmp_path: Path, monkeypatch):
    from aurora.dashboard import provider_settings as provider_settings_module
    from aurora.dashboard.provider_settings import DashboardProviderSettingsError, ProviderSettingsService
    from aurora.operator_settings import OperatorSettingsStore
    import pytest

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "deepseek": {
                "api_key": "stored-but-no-adapter",
                "base_url": "https://api.deepseek.com/v1",
                "enabled": True,
            },
        },
    })
    monkeypatch.setattr(
        provider_settings_module,
        "_urlopen",
        lambda _request: (_ for _ in ()).throw(AssertionError("must not attempt network")),
    )

    with pytest.raises(DashboardProviderSettingsError, match="adapter is not implemented"):
        ProviderSettingsService(store, runtime).test_provider("deepseek")


def test_provider_connection_probe_rejects_endpoint_with_embedded_credentials(tmp_path: Path, monkeypatch):
    from aurora.dashboard import provider_settings as provider_settings_module
    from aurora.dashboard.provider_settings import DashboardProviderSettingsError, ProviderSettingsService
    from aurora.operator_settings import OperatorSettingsStore
    import pytest

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    # Simulate a legacy or manually edited settings file. Current saves reject
    # embedded URL credentials; this also verifies the connection-test defense-in-depth.
    store.path.write_text(json.dumps({
        "providers": {
            "nvidia_api": {
                "api_key": "",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "enabled": True,
            },
            "openai": {
                "api_key": "stored-key",
                "base_url": "https://someone:password@api.openai.com/v1",
                "enabled": True,
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(
        provider_settings_module,
        "_urlopen",
        lambda _request: (_ for _ in ()).throw(AssertionError("must not attempt network")),
    )

    with pytest.raises(DashboardProviderSettingsError, match="without embedded credentials"):
        ProviderSettingsService(store, runtime).test_provider("openai")



def test_openrouter_connection_probe_uses_saved_endpoint_and_bearer_key(tmp_path: Path, monkeypatch):
    from aurora.dashboard import provider_settings as provider_settings_module
    from aurora.dashboard.provider_settings import ProviderSettingsService
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openrouter": {
                "api_key": "openrouter-credential-secret",
                "base_url": "https://openrouter-proxy.example/api/v1",
                "enabled": True,
            },
        },
    })
    captured = {}

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({"data": [{"id": "openrouter/free"}]}).encode()

    def fake_urlopen(request):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr(provider_settings_module, "_urlopen", fake_urlopen)
    result = ProviderSettingsService(store, runtime).test_provider("openrouter")

    assert captured["url"] == "https://openrouter-proxy.example/api/v1/models?limit=10"
    assert captured["authorization"] == "Bearer openrouter-credential-secret"
    assert result["ok"] is True
    assert result["provider"] == "openrouter"
    assert result["models"] == ["openrouter/free"]
    assert "openrouter-credential-secret" not in str(result)


def test_provider_connection_probe_reports_auth_failure_without_secret(tmp_path: Path, monkeypatch):
    import pytest
    from aurora.dashboard import provider_settings as provider_settings_module
    from aurora.dashboard.provider_settings import DashboardProviderSettingsError, ProviderSettingsService
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openai": {
                "api_key": "must-not-leak-this",
                "base_url": "https://api.openai.com/v1",
                "enabled": True,
            },
        },
    })

    def reject(_request):
        raise DashboardProviderSettingsError("401 provider response")

    monkeypatch.setattr(provider_settings_module, "_urlopen", reject)
    with pytest.raises(DashboardProviderSettingsError, match="401 provider response") as exc:
        ProviderSettingsService(store, runtime).test_provider("openai")
    assert "must-not-leak-this" not in str(exc.value)



def test_provider_http_status_diagnostics_are_specific_and_body_safe(monkeypatch):
    import io
    import pytest
    from aurora.dashboard import provider_settings as provider_settings_module
    from aurora.dashboard.provider_settings import DashboardProviderSettingsError

    cases = [
        (401, "authentication rejected"),
        (403, "access denied"),
        (404, "base URL"),
        (429, "rate limit or quota"),
        (503, "service unavailable"),
    ]
    for status, expected in cases:
        def reject(_request, timeout, current_status=status):
            raise provider_settings_module.urllib.error.HTTPError(
                "https://provider.example/v1/models",
                current_status,
                "provider error",
                {},
                io.BytesIO(b"private provider response body must not leak"),
            )

        monkeypatch.setattr(provider_settings_module.urllib.request, "urlopen", reject)
        request = provider_settings_module.urllib.request.Request(
            "https://provider.example/v1/models"
        )
        with pytest.raises(DashboardProviderSettingsError, match=expected) as exc:
            provider_settings_module._urlopen(request)
        assert "private provider response body must not leak" not in str(exc.value)



def test_live_inference_probe_uses_only_a_discovered_free_nvidia_model(tmp_path: Path):
    from types import SimpleNamespace
    from aurora.dashboard.provider_settings import ProviderSettingsService
    from aurora.inference.models import ModelSpec
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "nvidia_api": {
                "api_key": "nvidia-live-test-secret",
                "enabled": True,
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
        },
    })

    discovered = ModelSpec(
        id="nvidia/test-free-model",
        provider="nvidia_api",
        endpoint="https://integrate.api.nvidia.com/v1",
        capabilities=("chat", "reasoning"),
        enabled=False,
        pricing_tier="free",
    )

    class Discovery:
        def discover_and_stage(self, provider_id):
            assert provider_id == "nvidia_api"
            return (SimpleNamespace(model=discovered),)

    runtime.model_discovery = Discovery()
    captured = {}

    class FakeAdapter:
        def generate(self, request, *, selected_model, execution_id):
            captured["request"] = request
            captured["selected_model"] = selected_model
            captured["execution_id"] = execution_id
            return SimpleNamespace(
                content="AURORA SETTINGS LIVE INFERENCE OK",
                telemetry=SimpleNamespace(duration_ms=17),
            )

    runtime.director.planner.router.provider_registry.create = lambda model: FakeAdapter()
    result = ProviderSettingsService(store, runtime).test_inference("nvidia_api")

    assert result["ok"] is True
    assert result["inference_verified"] is True
    assert result["model"] == "nvidia/test-free-model"
    assert result["discovered_free_models"] == ["nvidia/test-free-model"]
    assert result["latency_ms"] == 17
    assert captured["selected_model"] == "nvidia/test-free-model"
    assert captured["request"].max_tokens == 32
    assert captured["execution_id"] == result["execution_id"]
    assert "nvidia-live-test-secret" not in str(result)


def test_live_inference_probe_uses_openrouter_free_and_returns_real_success_evidence(tmp_path: Path):
    from types import SimpleNamespace
    from aurora.dashboard.provider_settings import ProviderSettingsService
    from aurora.inference.providers.registry import default_provider_registry
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openrouter": {
                "api_key": "openrouter-live-test-secret",
                "base_url": "https://openrouter.ai/api/v1",
                "enabled": True,
            },
        },
    })
    runtime.director.planner.router.provider_registry = default_provider_registry(
        nvidia_api_key="nvidia-test",
        openrouter_api_key="openrouter-live-test-secret",
        include_openrouter=True,
    )
    captured = {}

    class FakeAdapter:
        def generate(self, request, *, selected_model, execution_id):
            captured["request"] = request
            captured["selected_model"] = selected_model
            return SimpleNamespace(
                content="AURORA SETTINGS LIVE INFERENCE OK",
                telemetry=SimpleNamespace(duration_ms=23),
            )

    runtime.director.planner.router.provider_registry.create = lambda model: FakeAdapter()
    result = ProviderSettingsService(store, runtime).test_inference("openrouter")

    assert result["ok"] is True
    assert result["inference_verified"] is True
    assert result["model"] == "openrouter/free"
    assert result["response_non_empty"] is True
    assert result["latency_ms"] == 23
    assert captured["selected_model"] == "openrouter/free"
    assert captured["request"].model == "openrouter/free"
    assert "openrouter-live-test-secret" not in str(result)


def test_live_inference_probe_refuses_unverified_paid_provider_routes(tmp_path: Path):
    import pytest
    from aurora.dashboard.provider_settings import DashboardProviderSettingsError, ProviderSettingsService
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openai": {
                "api_key": "openai-test-key",
                "base_url": "https://api.openai.com/v1",
                "enabled": True,
            },
        },
    })

    with pytest.raises(DashboardProviderSettingsError, match="free-model policy"):
        ProviderSettingsService(store, runtime).test_inference("openai")



def test_live_inference_probe_classifies_429_without_leaking_provider_details(tmp_path: Path):
    import pytest
    from aurora.dashboard.provider_settings import DashboardProviderSettingsError, ProviderSettingsService
    from aurora.inference.errors import ProviderErrorContext, ProviderHTTPError
    from aurora.inference.providers.registry import default_provider_registry
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "openrouter": {
                "api_key": "openrouter-secret-must-not-leak",
                "base_url": "https://openrouter.ai/api/v1",
                "enabled": True,
            },
        },
    })
    live_router = runtime.director.planner.router
    live_router.provider_registry = default_provider_registry(
        nvidia_api_key="nvidia-test",
        openrouter_api_key="openrouter-secret-must-not-leak",
        include_openrouter=True,
    )

    class RateLimitedAdapter:
        def generate(self, request, *, selected_model, execution_id):
            raise ProviderHTTPError(
                "upstream raw error should not be used",
                context=ProviderErrorContext(
                    provider="openrouter",
                    request_id=request.request_id,
                    execution_id=execution_id,
                    selected_model=selected_model,
                    provider_model=selected_model,
                ),
                status_code=429,
            )

    live_router.provider_registry.create = lambda _model: RateLimitedAdapter()
    with pytest.raises(DashboardProviderSettingsError, match="rate limit or quota") as exc:
        ProviderSettingsService(store, runtime).test_inference("openrouter")
    assert "openrouter-secret-must-not-leak" not in str(exc.value)
    assert "upstream raw error should not be used" not in str(exc.value)


def test_live_inference_probe_rejects_empty_response(tmp_path: Path):
    import pytest
    from types import SimpleNamespace
    from aurora.dashboard.provider_settings import DashboardProviderSettingsError, ProviderSettingsService
    from aurora.inference.models import ModelSpec
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "nvidia_api": {
                "api_key": "nvidia-test-key",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "enabled": True,
            },
        },
    })
    candidate = ModelSpec(
        id="nvidia/empty-response-test",
        provider="nvidia_api",
        endpoint="https://integrate.api.nvidia.com/v1",
        enabled=False,
        pricing_tier="free",
    )

    class Discovery:
        def discover_and_stage(self, provider_id):
            return (SimpleNamespace(model=candidate),)

    runtime.model_discovery = Discovery()

    class EmptyAdapter:
        def generate(self, request, *, selected_model, execution_id):
            return SimpleNamespace(content="  ", telemetry=None)

    runtime.director.planner.router.provider_registry.create = lambda _model: EmptyAdapter()
    with pytest.raises(DashboardProviderSettingsError, match="empty response"):
        ProviderSettingsService(store, runtime).test_inference("nvidia_api")


def test_live_inference_probe_never_sends_request_when_no_approved_free_model_exists(tmp_path: Path):
    import pytest
    from types import SimpleNamespace
    from aurora.dashboard.provider_settings import DashboardProviderSettingsError, ProviderSettingsService
    from aurora.inference.models import ModelSpec
    from aurora.operator_settings import OperatorSettingsStore

    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "nvidia_api": {
                "api_key": "nvidia-test-key",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "enabled": True,
            },
        },
    })
    candidate = ModelSpec(
        id="nvidia/not-approved-test",
        provider="nvidia_api",
        endpoint="https://integrate.api.nvidia.com/v1",
        enabled=False,
        pricing_tier="unknown",
    )

    class Discovery:
        def discover_and_stage(self, provider_id):
            return (SimpleNamespace(model=candidate),)

    runtime.model_discovery = Discovery()
    runtime.director.planner.router.provider_registry.create = lambda _model: (_ for _ in ()).throw(
        AssertionError("inference must not run without a free-price classification")
    )

    with pytest.raises(DashboardProviderSettingsError, match="no inference request was sent"):
        ProviderSettingsService(store, runtime).test_inference("nvidia_api")

def test_save_and_apply_preserves_canonical_nvidia_network_gateway(tmp_path: Path):
    runtime = Runtime(router(tmp_path))
    gateway = object()
    runtime.workbench = SimpleNamespace(gateway=gateway)
    runtime.model_discovery = SimpleNamespace(
        provider_registry=None,
        model_registry=None,
    )
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {"nvidia_api": {"api_key": "existing-nvidia-key"}},
    })
    service = ProviderSettingsService(store, runtime)

    service.save_and_apply({
        "director": {
            "provider": "nvidia_api",
            "model": "nvidia/nemotron-3-ultra-550b-a55b",
            "endpoint": "https://integrate.api.nvidia.com/v1",
        },
    })

    selected = runtime.director.planner.router.model_registry.resolve(
        "nvidia/nemotron-3-ultra-550b-a55b"
    )
    provider = runtime.director.planner.router.provider_registry.create(selected)

    assert provider.gateway is gateway
    assert provider.api_key == "existing-nvidia-key"
    assert runtime.model_discovery.provider_registry is (
        runtime.director.planner.router.provider_registry
    )
    assert runtime.model_discovery.model_registry is (
        runtime.director.planner.router.model_registry
    )


def test_saving_router_mode_with_nvidia_key_does_not_pin_alias_as_model(
    tmp_path: Path,
):
    runtime = Runtime(router(tmp_path))
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {"nvidia_api": {"api_key": "existing-nvidia-key"}},
    })
    service = ProviderSettingsService(store, runtime)

    state = service.save_and_apply({
        "director": {"provider": "router", "model": "router"},
    })

    assert state["director"]["provider"] == "router"
    assert state["director"]["model"] == "router"
