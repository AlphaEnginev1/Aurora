from __future__ import annotations

from pathlib import Path

import pytest

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.operator_settings import OperatorSettingsStore
from aurora.orchestration.director_provider_failover import (
    DirectorProviderCandidate,
    DirectorProviderFailoverController,
    DirectorProviderFailoverError,
    DirectorProviderFailoverStore,
)


class FakeProvider:
    def __init__(self, provider_id: str, responses: list[object]) -> None:
        self.provider_id = provider_id
        self._responses = list(responses)
        self.calls = 0

    def generate(self, *, messages, model, role, tools=()):
        self.calls += 1
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return DirectorModelResponse(
            content=str(result),
            model=model or f"{self.provider_id}-model",
            provider=self.provider_id,
            request_id=f"request-{self.calls}",
            execution_id=f"execution-{self.calls}",
            tool_calls=(),
        )


class ProviderFailure(RuntimeError):
    failoverable = True


def _candidate(provider_id: str, provider: FakeProvider, model: str) -> DirectorProviderCandidate:
    return DirectorProviderCandidate(
        provider_id=provider_id,
        model=model,
        endpoint=None,
        factory=lambda: provider,
    )


def test_factory_discovers_and_selects_available_nvidia_model_without_fixed_id(tmp_path: Path, monkeypatch):
    from aurora.inference.discovery import DiscoveredModelCandidate, candidate_id
    from aurora.inference.models import InferenceResponse, ModelSpec
    from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
    from aurora.inference.registry import ModelRegistry
    from aurora.inference.router import ModelRouter
    from aurora.orchestration.factory import _build_director_provider_failover

    class FakeNvidiaInferenceProvider:
        provider_id = "nvidia_api"

        def __init__(self, _model):
            pass

        def generate(self, request, *, selected_model, execution_id):
            return InferenceResponse(
                model=selected_model,
                provider="nvidia_api",
                content="NVIDIA dynamic replacement",
                request_id=request.request_id,
                execution_id=execution_id,
            )

    def factory(model):
        return FakeNvidiaInferenceProvider(model)

    provider_registry = ProviderRegistry((
        ProviderSpec(
            provider_id="nvidia_api",
            name="NVIDIA NIM API",
            capabilities=("chat", "reasoning"),
            enabled=True,
            factory=factory,
        ),
    ))
    model_registry = ModelRegistry([], provider_registry=provider_registry)
    router = ModelRouter(
        models=[],
        nvidia_api_key="nvidia-secret",
        provider_registry=provider_registry,
        model_registry=model_registry,
    )

    discovered = (
        DiscoveredModelCandidate(
            candidate_id=candidate_id("nvidia_api", "google/gemma-4-31b-it"),
            provider_id="nvidia_api",
            model=ModelSpec(
                id="google/gemma-4-31b-it",
                provider="nvidia_api",
                endpoint="https://integrate.api.nvidia.com/v1",
                capabilities=("reasoning",),
                quality_score=0.2,
            ),
            metadata={"free_endpoint_catalog": True},
            discovered_at="2026-10-05T00:00:00+00:00",
        ),
        DiscoveredModelCandidate(
            candidate_id=candidate_id("nvidia_api", "nvidia/nemotron-3-ultra-550b-a55b"),
            provider_id="nvidia_api",
            model=ModelSpec(
                id="nvidia/nemotron-3-ultra-550b-a55b",
                provider="nvidia_api",
                endpoint="https://integrate.api.nvidia.com/v1",
                capabilities=("reasoning",),
                quality_score=0.9,
            ),
            metadata={"free_endpoint_catalog": True},
            discovered_at="2026-10-05T00:00:00+00:00",
        ),
    )

    class FakeDiscovery:
        def __init__(self, *_args):
            pass

        def discover_and_stage(self, provider_id):
            assert provider_id == "nvidia_api"
            return discovered

    monkeypatch.setattr("aurora.orchestration.factory.ModelDiscoveryService", FakeDiscovery)
    monkeypatch.delenv("AURORA_NVIDIA_MODEL_ID", raising=False)

    class FailingRouter:
        provider_id = "inference_router"

        def generate(self, **_kwargs):
            raise ProviderFailure("forced router outage")

    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save({"providers": {"nvidia_api": {"api_key": "nvidia-secret"}}})
    controller = _build_director_provider_failover(
        settings_store=settings,
        router=router,
        active_provider=FailingRouter(),
        db_path=tmp_path / "tasks.db",
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="nvidia/nemotron-3-ultra-550b-a55b",
        role="director_planner",
    )

    assert response.provider == "nvidia_api"
    # The concrete model that failed in the routed primary attempt must not be retried.
    assert response.model == "google/gemma-4-31b-it"
    assert controller.provider_id == "nvidia_api"


def test_factory_ignores_historical_health_cooldown_when_building_reserve_order(tmp_path: Path, monkeypatch):
    from aurora.inference.discovery import DiscoveredModelCandidate, candidate_id
    from aurora.inference.models import InferenceResponse, ModelSpec
    from aurora.inference.provider_health import ProviderHealthStore
    from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
    from aurora.inference.registry import ModelRegistry
    from aurora.inference.router import ModelRouter
    from aurora.orchestration.factory import _build_director_provider_failover

    class FakeNvidiaInferenceProvider:
        provider_id = "nvidia_api"

        def __init__(self, _model):
            pass

        def generate(self, request, *, selected_model, execution_id):
            return InferenceResponse(
                model=selected_model,
                provider="nvidia_api",
                content="reserve survives historical cooldown",
                request_id=request.request_id,
                execution_id=execution_id,
            )

    provider_registry = ProviderRegistry((
        ProviderSpec(
            provider_id="nvidia_api",
            name="NVIDIA NIM API",
            capabilities=("chat", "reasoning"),
            enabled=True,
            factory=lambda model: FakeNvidiaInferenceProvider(model),
        ),
    ))
    model_registry = ModelRegistry([], provider_registry=provider_registry)
    router = ModelRouter(
        models=[],
        nvidia_api_key="nvidia-secret",
        provider_registry=provider_registry,
        model_registry=model_registry,
    )

    discovered = (
        DiscoveredModelCandidate(
            candidate_id=candidate_id("nvidia_api", "google/gemma-4-31b-it"),
            provider_id="nvidia_api",
            model=ModelSpec(
                id="google/gemma-4-31b-it",
                provider="nvidia_api",
                endpoint="https://integrate.api.nvidia.com/v1",
                capabilities=("reasoning",),
                quality_score=0.2,
                pricing_tier="free",
            ),
            metadata={"free_endpoint_catalog": True},
            discovered_at="2026-10-05T00:00:00+00:00",
        ),
        DiscoveredModelCandidate(
            candidate_id=candidate_id("nvidia_api", "nvidia/nemotron-3-ultra-550b-a55b"),
            provider_id="nvidia_api",
            model=ModelSpec(
                id="nvidia/nemotron-3-ultra-550b-a55b",
                provider="nvidia_api",
                endpoint="https://integrate.api.nvidia.com/v1",
                capabilities=("reasoning",),
                quality_score=0.9,
                pricing_tier="free",
            ),
            metadata={"free_endpoint_catalog": True},
            discovered_at="2026-10-05T00:00:00+00:00",
        ),
    )

    class FakeDiscovery:
        def __init__(self, *_args):
            pass

        def discover_and_stage(self, provider_id):
            assert provider_id == "nvidia_api"
            return discovered

    monkeypatch.setattr("aurora.orchestration.factory.ModelDiscoveryService", FakeDiscovery)

    health = ProviderHealthStore(tmp_path / "health.db", failure_threshold=1)
    from aurora.inference.errors import ProviderTimeoutError, ProviderErrorContext

    for model_id in ("google/gemma-4-31b-it", "nvidia/nemotron-3-ultra-550b-a55b"):
        health.record_failure(
            "nvidia_api",
            model_id,
            ProviderTimeoutError(
                "historical outage",
                context=ProviderErrorContext(
                    provider="nvidia_api",
                    request_id="historical",
                    execution_id="historical",
                    requested_model=model_id,
                    selected_model=model_id,
                    provider_model=model_id,
                ),
            ),
        )

    class FailingRouter:
        provider_id = "inference_router"

        def generate(self, **_kwargs):
            raise ProviderTimeoutError(
                "primary outage",
                context=ProviderErrorContext(
                    provider="nvidia_api",
                    request_id="primary",
                    execution_id="primary",
                    requested_model=None,
                    selected_model="nvidia/nemotron-3-ultra-550b-a55b",
                    provider_model="nvidia/nemotron-3-ultra-550b-a55b",
                ),
            )

    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save({"providers": {"nvidia_api": {"api_key": "nvidia-secret"}}})
    controller = _build_director_provider_failover(
        settings_store=settings,
        router=router,
        active_provider=FailingRouter(),
        db_path=tmp_path / "tasks.db",
        health_store=health,
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "hello"}],
        model=None,
        role="director_conversation",
    )

    assert response.provider == "nvidia_api"
    assert response.model == "google/gemma-4-31b-it"
    assert response.content == "reserve survives historical cooldown"


def test_dynamic_failure_context_excludes_the_concrete_failed_model(tmp_path: Path):
    from aurora.inference.errors import ProviderErrorContext, ProviderTimeoutError

    failed = FakeProvider("nvidia_api", ["should not be called"])
    reserve = FakeProvider("nvidia_api", ["reserve works"])

    error = ProviderTimeoutError(
        "primary timed out",
        context=ProviderErrorContext(
            provider="nvidia_api",
            request_id="request-primary",
            execution_id="execution-primary",
            requested_model=None,
            selected_model="nvidia/model-a",
            provider_model="nvidia/model-a",
        ),
    )

    class DynamicPrimary:
        provider_id = "inference_router"

        def generate(self, **_kwargs):
            raise error

    controller = DirectorProviderFailoverController(
        active_provider=DynamicPrimary(),
        candidates=(
            _candidate("nvidia_api", failed, "nvidia/model-a"),
            _candidate("nvidia_api", reserve, "nvidia/model-b"),
        ),
        store=DirectorProviderFailoverStore(str(tmp_path / "runtime.db")),
        activate=lambda _candidate, _provider: None,
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model=None,
        role="director_conversation",
    )

    assert response.provider == "nvidia_api"
    assert response.content == "reserve works"
    assert failed.calls == 0
    assert reserve.calls == 1


def test_factory_uses_cached_nvidia_catalog_when_live_discovery_is_unavailable(tmp_path: Path, monkeypatch):
    from aurora.inference.discovery import DiscoveredModelCandidate, ModelCandidateStore, candidate_id
    from aurora.inference.models import InferenceResponse, ModelSpec
    from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
    from aurora.inference.registry import ModelRegistry
    from aurora.inference.router import ModelRouter
    from aurora.orchestration.factory import _build_director_provider_failover

    class FakeNvidiaInferenceProvider:
        provider_id = "nvidia_api"

        def __init__(self, _model):
            pass

        def generate(self, request, *, selected_model, execution_id):
            return InferenceResponse(
                model=selected_model,
                provider="nvidia_api",
                content="cached NVIDIA replacement",
                request_id=request.request_id,
                execution_id=execution_id,
            )

    provider_registry = ProviderRegistry((
        ProviderSpec(
            provider_id="nvidia_api",
            name="NVIDIA NIM API",
            capabilities=("chat", "reasoning"),
            enabled=True,
            factory=lambda model: FakeNvidiaInferenceProvider(model),
        ),
    ))
    model_registry = ModelRegistry([], provider_registry=provider_registry)
    router = ModelRouter(
        models=[],
        nvidia_api_key="nvidia-secret",
        provider_registry=provider_registry,
        model_registry=model_registry,
    )

    cached = DiscoveredModelCandidate(
        candidate_id=candidate_id("nvidia_api", "nvidia/nemotron-3-super-120b-a12b"),
        provider_id="nvidia_api",
        model=ModelSpec(
            id="nvidia/nemotron-3-super-120b-a12b",
            provider="nvidia_api",
            endpoint="https://integrate.api.nvidia.com/v1",
            capabilities=("reasoning",),
        ),
        metadata={"free_endpoint_catalog": True},
        discovered_at="2026-10-05T00:00:00+00:00",
    )
    cache = ModelCandidateStore(tmp_path / "model-candidates.db")
    cache.upsert(cached)

    monkeypatch.setattr(
        "aurora.orchestration.factory.ModelDiscoveryService.discover_and_stage",
        lambda _self, _provider_id: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )

    class FailingRouter:
        provider_id = "inference_router"

        def generate(self, **_kwargs):
            raise ProviderFailure("forced router outage")

    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save({"providers": {"nvidia_api": {"api_key": "nvidia-secret"}}})
    controller = _build_director_provider_failover(
        settings_store=settings,
        router=router,
        active_provider=FailingRouter(),
        db_path=tmp_path / "tasks.db",
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="nvidia/nemotron-3-ultra-550b-a55b",
        role="director_conversation",
    )

    assert response.provider == "nvidia_api"
    assert response.model == "nvidia/nemotron-3-super-120b-a12b"
    assert response.content == "cached NVIDIA replacement"


def test_failures_are_tracked_per_nvidia_model_not_per_provider(tmp_path: Path):
    router = FakeProvider("inference_router", [ProviderFailure("forced router outage")])
    first = FakeProvider("nvidia_api", [ProviderFailure("model A outage")])
    second = FakeProvider("nvidia_api", ["model B works"])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    controller = DirectorProviderFailoverController(
        active_provider=router,
        candidates=(),
        store=store,
        activate=lambda _candidate, _provider: None,
        candidate_resolver=lambda _messages, _model, _role: (
            _candidate("nvidia_api", first, "nvidia/model-a"),
            _candidate("nvidia_api", second, "nvidia/model-b"),
        ),
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="router/test-model",
        role="director_planner",
    )

    assert response.provider == "nvidia_api"
    assert response.content == "model B works"
    assert first.calls == 1
    assert second.calls == 1


def test_transient_failure_of_one_same_provider_model_continues_to_next_model(tmp_path: Path):
    router = FakeProvider("inference_router", [ProviderFailure("forced router outage")])

    from aurora.inference.errors import ProviderTimeoutError, ProviderErrorContext

    timeout_error = ProviderTimeoutError(
        "model A timed out",
        context=ProviderErrorContext(
            provider="nvidia_api",
            request_id="request-a",
            execution_id="execution-a",
            requested_model=None,
            selected_model="nvidia/model-a",
            provider_model="nvidia/model-a",
        ),
    )
    first = FakeProvider("nvidia_api", [timeout_error])
    second = FakeProvider("nvidia_api", ["model B works"])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    controller = DirectorProviderFailoverController(
        active_provider=router,
        candidates=(),
        store=store,
        activate=lambda _candidate, _provider: None,
        candidate_resolver=lambda _messages, _model, _role: (
            _candidate("nvidia_api", first, "nvidia/model-a"),
            _candidate("nvidia_api", second, "nvidia/model-b"),
        ),
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="router/test-model",
        role="director_conversation",
    )

    assert response.provider == "nvidia_api"
    assert response.content == "model B works"
    assert first.calls == 1
    assert second.calls == 1


def test_router_failure_switches_to_alternative_and_persists_transition(tmp_path: Path):
    router = FakeProvider("inference_router", [ProviderFailure("forced router outage")])
    nvidia = FakeProvider("nvidia_api", ["alternative answer"])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    activated = []
    controller = DirectorProviderFailoverController(
        active_provider=router,
        candidates=(_candidate("nvidia_api", nvidia, "poolside/laguna-xs-2.1"),),
        store=store,
        activate=lambda candidate, provider: activated.append((candidate, provider)),
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="router/test-model",
        role="director_planner",
    )

    assert response.provider == "nvidia_api"
    assert controller.provider_id == "nvidia_api"
    assert activated[0][0].provider_id == "nvidia_api"
    evidence = store.list_recent(limit=20)
    phases = [row["phase"] for row in evidence]
    assert "failure_detected" in phases
    assert "attempted" in phases
    assert "succeeded" in phases


def test_all_candidates_fail_closed_without_activation(tmp_path: Path):
    router = FakeProvider("inference_router", [ProviderFailure("forced router outage")])
    nvidia = FakeProvider("nvidia_api", [ProviderFailure("NVIDIA outage")])
    router = FakeProvider("inference_router", [ProviderFailure("router outage")])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    activated = []
    controller = DirectorProviderFailoverController(
        active_provider=router,
        candidates=(
            _candidate("nvidia_api", nvidia, "poolside/laguna-xs-2.1"),
            _candidate("inference_router", router, "router-model"),
        ),
        store=store,
        activate=lambda candidate, provider: activated.append((candidate, provider)),
    )

    with pytest.raises(DirectorProviderFailoverError):
        controller.generate(
            messages=[{"role": "user", "content": "continue"}],
            model="router/test-model",
            role="director_reviewer",
        )

    assert activated == []
    assert controller.provider_id == "inference_router"
    assert any(row["phase"] == "exhausted" for row in store.list_recent(limit=20))


def test_non_failoverable_provider_error_is_not_masked(tmp_path: Path):
    class ClientError(RuntimeError):
        failoverable = False

    router = FakeProvider("inference_router", [ClientError("invalid request")])
    nvidia = FakeProvider("nvidia_api", ["should not run"])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    controller = DirectorProviderFailoverController(
        active_provider=router,
        candidates=(_candidate("nvidia_api", nvidia, "nvidia/test-model"),),
        store=store,
        activate=lambda _candidate, _provider: None,
    )

    with pytest.raises(ClientError):
        controller.generate(
            messages=[{"role": "user", "content": "invalid"}],
            model="router/test-model",
            role="director_planner",
        )

    assert nvidia.calls == 0


def test_candidate_is_not_active_until_success(tmp_path: Path):
    router = FakeProvider("inference_router", [ProviderFailure("forced")])
    nvidia = FakeProvider("nvidia_api", [ProviderFailure("still down")])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    activated = []
    controller = DirectorProviderFailoverController(
        active_provider=router,
        candidates=(_candidate("nvidia_api", nvidia, "nvidia/test-model"),),
        store=store,
        activate=lambda candidate, provider: activated.append(candidate.provider_id),
    )

    with pytest.raises(DirectorProviderFailoverError):
        controller.generate(
            messages=[{"role": "user", "content": "first"}],
            model="router/test-model",
            role="director_reviewer",
        )

    assert activated == []
    assert controller.provider_id == "inference_router"


def test_successful_nvidia_failover_persists_and_reloads_after_restart(tmp_path: Path):
    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save(
        {
            "director": {
                "provider": "router",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "endpoint": "https://generativelanguage.googleapis.com/v1beta",
                "failover": {"enabled": True, "order": ["nvidia_api"]},
            },
            "providers": {
                "nvidia_api": {
                    "api_key": "nvidia-secret",
                    "base_url": "https://integrate.api.nvidia.com/v1",
                },
            },
        }
    )
    router = FakeProvider("inference_router", [ProviderFailure("forced outage")])
    nvidia = FakeProvider("nvidia_api", ["replacement"])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))

    activated = []

    def activate(candidate, _provider):
        settings.update_from_form(
            {
                "director": {
                    "provider": candidate.provider_id,
                    "model": candidate.model or "router",
                    "endpoint": candidate.endpoint or "https://example.invalid",
                }
            }
        )
        activated.append(candidate.provider_id)

    controller = DirectorProviderFailoverController(
        active_provider=router,
        candidates=(
            _candidate("nvidia_api", nvidia, "poolside/laguna-xs-2.1"),
        ),
        store=store,
        activate=activate,
    )
    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="nvidia/nemotron-3-ultra-550b-a55b",
        role="director_planner",
    )

    assert response.provider == "nvidia_api"
    assert activated == ["nvidia_api"]
    persisted = settings.load()["director"]
    assert persisted["provider"] == "nvidia_api"
    assert persisted["model"] == "poolside/laguna-xs-2.1"

    from aurora.orchestration.factory import _build_director_provider

    restarted_provider = _build_director_provider(settings, router=object())
    assert restarted_provider is not None
    assert restarted_provider.provider_id == "nvidia_api"

def test_factory_uses_explicit_openrouter_free_reserve_and_persists_activation(
    tmp_path: Path, monkeypatch
):
    from aurora.inference.models import InferenceResponse, ModelSpec
    from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
    from aurora.inference.registry import ModelRegistry
    from aurora.inference.router import ModelRouter
    from aurora.orchestration.factory import _build_director_provider_failover

    class FakeOpenRouterInferenceProvider:
        provider_id = "openrouter"

        def __init__(self, _model):
            pass

        def generate(self, request, *, selected_model, execution_id):
            return InferenceResponse(
                model=selected_model,
                provider="openrouter",
                content="authorized OpenRouter free reserve succeeded",
                request_id=request.request_id,
                execution_id=execution_id,
                selected_model=selected_model,
                provider_model=selected_model,
                reported_model=selected_model,
            )

    registry = ProviderRegistry((
        ProviderSpec(
            provider_id="openrouter",
            name="OpenRouter free route",
            capabilities=("chat", "reasoning"),
            enabled=True,
            factory=lambda model: FakeOpenRouterInferenceProvider(model),
        ),
    ))
    free_model = ModelSpec(
        id="openrouter/free",
        provider="openrouter",
        endpoint="https://openrouter.ai/api/v1",
        capabilities=("chat", "reasoning"),
        specialties=("reasoning", "planning"),
        enabled=True,
        healthy=True,
        available=True,
        pricing_tier="free",
        cost_score=1.0,
    )
    model_registry = ModelRegistry([free_model], provider_registry=registry)
    router = ModelRouter(
        models=[free_model],
        provider_registry=registry,
        model_registry=model_registry,
        cost_policy="free_only",
    )

    class EmptyNvidiaDiscovery:
        def __init__(self, *_args):
            pass

        def refresh_all(self, **_kwargs):
            return {"nvidia_api": ()}

    monkeypatch.setattr(
        "aurora.orchestration.factory.ModelDiscoveryService",
        EmptyNvidiaDiscovery,
    )

    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save({
        "director": {
            "failover": {"enabled": True, "order": ["openrouter", "nvidia_api"]},
        },
        "providers": {
            "nvidia_api": {"api_key": "nvidia-test"},
            "openrouter": {
                "api_key": "openrouter-test",
                "base_url": "https://openrouter.ai/api/v1",
            },
        },
    })

    class FailingRouter:
        provider_id = "inference_router"

        def generate(self, **_kwargs):
            raise ProviderFailure("forced primary router outage")

    controller = _build_director_provider_failover(
        settings_store=settings,
        router=router,
        active_provider=FailingRouter(),
        db_path=tmp_path / "tasks.db",
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue the accepted mission"}],
        model="router/test-model",
        role="director_conversation",
    )

    assert response.provider == "openrouter"
    assert response.model == "openrouter/free"
    assert response.content == "authorized OpenRouter free reserve succeeded"
    assert controller.provider_id == "openrouter"
    persisted = settings.load()["director"]
    assert persisted["provider"] == "openrouter"
    assert persisted["model"] == "openrouter/free"
    assert persisted["failover"]["order"] == ["openrouter", "nvidia_api"]

def test_bound_provider_failure_uses_concrete_model_and_skips_same_model_reserve(
    tmp_path: Path,
):
    from aurora.inference.errors import ProviderErrorContext, ProviderTimeoutError
    from aurora.inference.router import ModelRouter
    from aurora.orchestration.director_provider_failover import BoundInferenceDirectorProvider

    class FailingRouter:
        def generate(self, request, **_kwargs):
            raise ProviderTimeoutError(
                "free route timed out",
                context=ProviderErrorContext(
                    provider="openrouter",
                    request_id=request.request_id,
                    execution_id="exec-openrouter-failed",
                    requested_model=request.model,
                    selected_model="openrouter/free",
                    provider_model="openrouter/free",
                ),
            )

    # BoundInferenceDirectorProvider only needs the router's generate contract
    # in this test; no network adapter or live provider call is made.
    active = BoundInferenceDirectorProvider(
        FailingRouter(),
        provider_id="openrouter",
        model="openrouter/free",
    )
    same_model = FakeProvider("openrouter", ["must never be retried"])
    reserve = FakeProvider("nvidia_api", ["NVIDIA reserve response"])

    controller = DirectorProviderFailoverController(
        active_provider=active,
        candidates=(
            _candidate("openrouter", same_model, "openrouter/free"),
            _candidate("nvidia_api", reserve, "nvidia/test-free-model"),
        ),
        store=DirectorProviderFailoverStore(str(tmp_path / "runtime.db")),
        activate=lambda _candidate, _provider: None,
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue accepted work"}],
        model="router",  # A router alias must not overwrite the bound model identity.
        role="director_conversation",
    )

    assert response.provider == "nvidia_api"
    assert response.content == "NVIDIA reserve response"
    assert same_model.calls == 0
    assert reserve.calls == 1
    assert active.model == "openrouter/free"

def test_failed_reserve_can_recover_on_a_later_request(tmp_path: Path):
    primary = FakeProvider(
        "inference_router",
        [
            ProviderFailure("primary unavailable on first request"),
            ProviderFailure("primary unavailable on second request"),
        ],
    )
    reserve = FakeProvider(
        "nvidia_api",
        [
            ProviderFailure("reserve temporarily unavailable"),
            "reserve recovered",
        ],
    )
    controller = DirectorProviderFailoverController(
        active_provider=primary,
        candidates=(_candidate("nvidia_api", reserve, "nvidia/model-a"),),
        store=DirectorProviderFailoverStore(str(tmp_path / "runtime.db")),
        activate=lambda _candidate, _provider: None,
    )

    with pytest.raises(DirectorProviderFailoverError):
        controller.generate(
            messages=[{"role": "user", "content": "first request"}],
            model=None,
            role="director_conversation",
        )

    recovered = controller.generate(
        messages=[{"role": "user", "content": "later request after recovery"}],
        model=None,
        role="director_conversation",
    )

    assert recovered.content == "reserve recovered"
    assert primary.calls == 2
    assert reserve.calls == 2
    assert controller.provider_id == "nvidia_api"

