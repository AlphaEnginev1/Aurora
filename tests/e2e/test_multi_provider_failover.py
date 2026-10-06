from __future__ import annotations

from pathlib import Path
import pytest

from aurora.inference.errors import ProviderHTTPError, ProviderErrorContext
from aurora.inference.provider_health import ProviderHealthStore
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.director_provider_failover import (
    DirectorProviderCandidate,
    DirectorProviderFailoverController,
    DirectorProviderFailoverError,
    DirectorProviderFailoverStore,
)


class FakeProvider:
    def __init__(self, provider_id: str, result: object):
        self.provider_id = provider_id
        self.result = result
        self.calls = 0

    def generate(self, *, messages, model, role, tools=()):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return DirectorModelResponse(
            content=str(self.result),
            model=model or "model",
            provider=self.provider_id,
            request_id=f"request-{self.calls}",
            execution_id=f"execution-{self.calls}",
            tool_calls=(),
        )


def _candidate(provider: FakeProvider, model: str) -> DirectorProviderCandidate:
    return DirectorProviderCandidate(
        provider_id=provider.provider_id,
        model=model,
        endpoint=None,
        factory=lambda p=provider: p,
    )


def test_nvidia_model_outage_continues_to_next_model(tmp_path: Path):
    ctx = ProviderErrorContext("nvidia_api", "req", "exec", selected_model="nvidia/model-a")
    active = FakeProvider("inference_router", ProviderHTTPError("503", context=ctx, status_code=503))
    nvidia_a = FakeProvider("nvidia_api", ProviderHTTPError("503", context=ctx, status_code=503))
    nvidia_b = FakeProvider("nvidia_api", "nvidia reserve continuation")
    health = ProviderHealthStore(tmp_path / "health.db", failure_threshold=1)
    controller = DirectorProviderFailoverController(
        active_provider=active,
        candidates=(_candidate(nvidia_a, "nvidia/model-a"), _candidate(nvidia_b, "nvidia/model-b")),
        store=DirectorProviderFailoverStore(str(tmp_path / "transitions.db")),
        activate=lambda _candidate, _provider: None,
        health_store=health,
    )
    result = controller.generate(
        messages=[{"role": "user", "content": "continue mission"}],
        model=None,
        role="director_planner",
    )
    assert result.provider == "nvidia_api"
    assert result.content == "nvidia reserve continuation"
    assert nvidia_a.calls == 1
    assert nvidia_b.calls == 1
    assert health.snapshot("nvidia_api", "nvidia/model-a").state == "cooldown"


def test_nvidia_all_reserve_models_fail_closed(tmp_path: Path):
    ctx = ProviderErrorContext("nvidia_api", "req", "exec")
    models = [
        FakeProvider("nvidia_api", ProviderHTTPError("503", context=ctx, status_code=503)),
        FakeProvider("nvidia_api", ProviderHTTPError("503", context=ctx, status_code=503)),
    ]
    controller = DirectorProviderFailoverController(
        active_provider=FakeProvider("inference_router", ProviderHTTPError("503", context=ctx, status_code=503)),
        candidates=tuple(_candidate(item, f"nvidia/model-{idx}") for idx, item in enumerate(models)),
        store=DirectorProviderFailoverStore(str(tmp_path / "transitions.db")),
        activate=lambda *_args: pytest.fail("failed NVIDIA model must never activate"),
        health_store=ProviderHealthStore(tmp_path / "health.db", failure_threshold=1),
    )
    with pytest.raises(DirectorProviderFailoverError):
        controller.generate(
            messages=[{"role": "user", "content": "continue mission"}],
            model=None,
            role="director_planner",
        )
    assert [item.calls for item in models] == [1, 1]
