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
            candidate_id=candidate_id("nvidia_api", "model/slow"),
            provider_id="nvidia_api",
            model=ModelSpec(
                id="model/slow",
                provider="nvidia_api",
                endpoint="https://integrate.api.nvidia.com/v1",
                capabilities=("reasoning",),
                quality_score=0.2,
            ),
            metadata={},
            discovered_at="2026-10-05T00:00:00+00:00",
        ),
        DiscoveredModelCandidate(
            candidate_id=candidate_id("nvidia_api", "model/best"),
            provider_id="nvidia_api",
            model=ModelSpec(
                id="model/best",
                provider="nvidia_api",
                endpoint="https://integrate.api.nvidia.com/v1",
                capabilities=("reasoning",),
                quality_score=0.9,
            ),
            metadata={},
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

    class FailingGemini:
        provider_id = "gemini"

        def generate(self, **_kwargs):
            raise ProviderFailure("forced Gemini outage")

    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save({"providers": {"nvidia_api": {"api_key": "nvidia-secret"}}})
    controller = _build_director_provider_failover(
        settings_store=settings,
        router=router,
        active_provider=FailingGemini(),
        db_path=tmp_path / "tasks.db",
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="gemini-3.7-flash",
        role="director_planner",
    )

    assert response.provider == "nvidia_api"
    assert response.model == "model/best"
    assert controller.provider_id == "nvidia_api"


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
        candidate_id=candidate_id("nvidia_api", "cached/model"),
        provider_id="nvidia_api",
        model=ModelSpec(
            id="cached/model",
            provider="nvidia_api",
            endpoint="https://integrate.api.nvidia.com/v1",
            capabilities=("reasoning",),
        ),
        metadata={},
        discovered_at="2026-10-05T00:00:00+00:00",
    )
    cache = ModelCandidateStore(tmp_path / "model-candidates.db")
    cache.upsert(cached)

    monkeypatch.setattr(
        "aurora.orchestration.factory.ModelDiscoveryService.discover_and_stage",
        lambda _self, _provider_id: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )

    class FailingGemini:
        provider_id = "gemini"

        def generate(self, **_kwargs):
            raise ProviderFailure("forced Gemini outage")

    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save({"providers": {"nvidia_api": {"api_key": "nvidia-secret"}}})
    controller = _build_director_provider_failover(
        settings_store=settings,
        router=router,
        active_provider=FailingGemini(),
        db_path=tmp_path / "tasks.db",
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="gemini-3.7-flash",
        role="director_conversation",
    )

    assert response.provider == "nvidia_api"
    assert response.model == "cached/model"
    assert response.content == "cached NVIDIA replacement"


def test_failures_are_tracked_per_nvidia_model_not_per_provider(tmp_path: Path):
    gemini = FakeProvider("gemini", [ProviderFailure("forced Gemini outage")])
    first = FakeProvider("nvidia_api", [ProviderFailure("model A outage")])
    second = FakeProvider("nvidia_api", ["model B works"])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
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
        model="gemini/test-model",
        role="director_planner",
    )

    assert response.provider == "nvidia_api"
    assert response.content == "model B works"
    assert first.calls == 1
    assert second.calls == 1


def test_gemini_failure_switches_to_alternative_and_persists_transition(tmp_path: Path):
    gemini = FakeProvider("gemini", [ProviderFailure("forced Gemini outage")])
    nvidia = FakeProvider("nvidia_api", ["alternative answer"])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    activated = []
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=(_candidate("nvidia_api", nvidia, "nvidia/test-model"),),
        store=store,
        activate=lambda candidate, provider: activated.append((candidate, provider)),
    )

    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="gemini/test-model",
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
    gemini = FakeProvider("gemini", [ProviderFailure("forced Gemini outage")])
    nvidia = FakeProvider("nvidia_api", [ProviderFailure("NVIDIA outage")])
    router = FakeProvider("inference_router", [ProviderFailure("router outage")])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    activated = []
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=(
            _candidate("nvidia_api", nvidia, "nvidia/test-model"),
            _candidate("inference_router", router, "router-model"),
        ),
        store=store,
        activate=lambda candidate, provider: activated.append((candidate, provider)),
    )

    with pytest.raises(DirectorProviderFailoverError):
        controller.generate(
            messages=[{"role": "user", "content": "continue"}],
            model="gemini/test-model",
            role="director_reviewer",
        )

    assert activated == []
    assert controller.provider_id == "gemini"
    assert any(row["phase"] == "exhausted" for row in store.list_recent(limit=20))


def test_non_failoverable_provider_error_is_not_masked(tmp_path: Path):
    class ClientError(RuntimeError):
        failoverable = False

    gemini = FakeProvider("gemini", [ClientError("invalid request")])
    nvidia = FakeProvider("nvidia_api", ["should not run"])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=(_candidate("nvidia_api", nvidia, "nvidia/test-model"),),
        store=store,
        activate=lambda _candidate, _provider: None,
    )

    with pytest.raises(ClientError):
        controller.generate(
            messages=[{"role": "user", "content": "invalid"}],
            model="gemini/test-model",
            role="director_planner",
        )

    assert nvidia.calls == 0


def test_candidate_is_not_active_until_success(tmp_path: Path):
    gemini = FakeProvider("gemini", [ProviderFailure("forced")])
    nvidia = FakeProvider("nvidia_api", [ProviderFailure("still down")])
    store = DirectorProviderFailoverStore(str(tmp_path / "runtime.db"))
    activated = []
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=(_candidate("nvidia_api", nvidia, "nvidia/test-model"),),
        store=store,
        activate=lambda candidate, provider: activated.append(candidate.provider_id),
    )

    with pytest.raises(DirectorProviderFailoverError):
        controller.generate(
            messages=[{"role": "user", "content": "first"}],
            model="gemini/test-model",
            role="director_reviewer",
        )

    assert activated == []
    assert controller.provider_id == "gemini"


def test_successful_nvidia_failover_persists_and_reloads_after_restart(tmp_path: Path):
    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save(
        {
            "director": {
                "provider": "gemini",
                "model": "gemini-3.7-flash",
                "endpoint": "https://generativelanguage.googleapis.com/v1beta",
                "failover": {"enabled": True, "order": ["nvidia_api", "router"]},
            },
            "providers": {
                "gemini": {"api_key": "gemini-secret"},
                "nvidia_api": {
                    "api_key": "nvidia-secret",
                    "base_url": "https://integrate.api.nvidia.com/v1",
                },
            },
        }
    )
    gemini = FakeProvider("gemini", [ProviderFailure("forced outage")])
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
        active_provider=gemini,
        candidates=(
            _candidate("nvidia_api", nvidia, "nvidia/test-model"),
        ),
        store=store,
        activate=activate,
    )
    response = controller.generate(
        messages=[{"role": "user", "content": "continue"}],
        model="gemini-3.7-flash",
        role="director_planner",
    )

    assert response.provider == "nvidia_api"
    assert activated == ["nvidia_api"]
    persisted = settings.load()["director"]
    assert persisted["provider"] == "nvidia_api"
    assert persisted["model"] == "nvidia/test-model"

    from aurora.orchestration.factory import _build_director_provider

    restarted_provider = _build_director_provider(settings, router=object())
    assert restarted_provider is not None
    assert restarted_provider.provider_id == "nvidia_api"
