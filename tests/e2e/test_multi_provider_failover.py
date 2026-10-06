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
            model=model or f"{self.provider_id}-model",
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


def test_multiple_provider_outage_continues_to_next_provider(tmp_path: Path):
    ctx = ProviderErrorContext("gemini", "req", "exec", selected_model="gemini-model")
    gemini = FakeProvider("gemini", ProviderHTTPError("503", context=ctx, status_code=503))
    nvidia = FakeProvider("nvidia_api", ProviderHTTPError("503", context=ctx, status_code=503))
    openai = FakeProvider("openai", ProviderHTTPError("429", context=ctx, status_code=429))
    anthropic = FakeProvider("anthropic", "continuation")

    health = ProviderHealthStore(tmp_path / "tasks.db", failure_threshold=1)
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=(
            _candidate(nvidia, "nvidia/model-a"),
            _candidate(openai, "gpt/model-a"),
            _candidate(anthropic, "claude/model-a"),
        ),
        store=DirectorProviderFailoverStore(str(tmp_path / "tasks.db")),
        activate=lambda _candidate, _provider: None,
        health_store=health,
    )

    result = controller.generate(
        messages=[{"role": "user", "content": "continue mission"}],
        model="gemini-model",
        role="director_planner",
    )

    assert result.provider == "anthropic"
    assert nvidia.calls == 1
    assert openai.calls == 0
    assert anthropic.calls == 1
    assert health.snapshot("gemini", "gemini-model").state == "cooldown"
    assert health.snapshot("nvidia_api", "nvidia/model-a").state == "cooldown"


def test_multiple_provider_outage_fails_closed_when_all_are_down(tmp_path: Path):
    ctx = ProviderErrorContext("test", "req", "exec")
    providers = [
        FakeProvider("nvidia_api", ProviderHTTPError("503", context=ctx, status_code=503)),
        FakeProvider("openai", ProviderHTTPError("503", context=ctx, status_code=503)),
        FakeProvider("anthropic", ProviderHTTPError("503", context=ctx, status_code=503)),
    ]
    health = ProviderHealthStore(tmp_path / "health.db", failure_threshold=1)
    controller = DirectorProviderFailoverController(
        active_provider=FakeProvider("gemini", ProviderHTTPError("503", context=ctx, status_code=503)),
        candidates=tuple(_candidate(item, f"{item.provider_id}/model") for item in providers),
        store=DirectorProviderFailoverStore(str(tmp_path / "transitions.db")),
        activate=lambda *_args: pytest.fail("failed provider must never activate"),
        health_store=health,
    )

    with pytest.raises(DirectorProviderFailoverError):
        controller.generate(
            messages=[{"role": "user", "content": "continue mission"}],
            model="gemini-model",
            role="director_planner",
        )
    assert all(item.calls == 1 for item in providers)
