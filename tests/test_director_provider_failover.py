from __future__ import annotations

from pathlib import Path

import pytest

from aurora.orchestration.director_provider import DirectorModelResponse
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
