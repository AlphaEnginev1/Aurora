from __future__ import annotations

import os

import pytest

from aurora.inference.models import ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.director_provider_failover import (
    BoundInferenceDirectorProvider,
    DirectorProviderCandidate,
    DirectorProviderFailoverController,
    DirectorProviderFailoverStore,
)
from aurora.orchestration.gemini_provider import GeminiDirectorProvider


class ForcedGeminiFailure(RuntimeError):
    failoverable = True


class OneShotGeminiFailureProxy:
    provider_id = "gemini"

    def __init__(self, provider: GeminiDirectorProvider) -> None:
        self.provider = provider
        self.calls = 0

    def generate(self, **kwargs) -> DirectorModelResponse:
        self.calls += 1
        if self.calls == 1:
            return self.provider.generate(**kwargs)
        raise ForcedGeminiFailure("controlled Increment 12 Gemini failure")


@pytest.mark.live
def test_live_gemini_to_nvidia_provider_failover(tmp_path):
    if os.getenv("AURORA_DIRECTOR_PROVIDER_FAILOVER_LIVE") != "1":
        pytest.skip("live provider failover gate is opt-in")

    gemini_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL_ID")
    nvidia_key = os.getenv("NVIDIA_API_KEY")
    nvidia_model = os.getenv("NVIDIA_MODEL_ID")
    if not gemini_key or not gemini_model:
        raise AssertionError("live provider failover gate requires GEMINI_API_KEY and GEMINI_MODEL_ID")
    if not nvidia_key or not nvidia_model:
        raise AssertionError("live provider failover gate requires NVIDIA_API_KEY and NVIDIA_MODEL_ID")

    gemini = OneShotGeminiFailureProxy(GeminiDirectorProvider())
    nvidia_router = ModelRouter(
        [
            ModelSpec(
                nvidia_model,
                "nvidia_api",
                os.getenv("NVIDIA_API_ENDPOINT", "https://integrate.api.nvidia.com/v1"),
                quality_score=0.8,
                latency_score=0.8,
                cost_score=0.8,
                specialties=("reasoning",),
            )
        ],
        nvidia_api_key=nvidia_key,
    )
    nvidia = BoundInferenceDirectorProvider(
        nvidia_router,
        provider_id="nvidia_api",
        model=nvidia_model,
    )
    failover_store = DirectorProviderFailoverStore(str(tmp_path / "live-provider-failover.db"))
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=(
            DirectorProviderCandidate(
                provider_id="nvidia_api",
                model=nvidia_model,
                endpoint=os.getenv("NVIDIA_API_ENDPOINT", "https://integrate.api.nvidia.com/v1"),
                factory=lambda: nvidia,
            ),
        ),
        store=failover_store,
        activate=lambda _candidate, _provider: None,
    )

    messages = [
        {"role": "system", "content": "You are Aurora Director. Respond with one short sentence."},
        {"role": "user", "content": "Confirm that the live Increment 12 failover gate is running."},
    ]

    first = controller.generate(
        messages=messages,
        model=gemini_model,
        role="director_planner",
    )
    second = controller.generate(
        messages=messages,
        model=gemini_model,
        role="director_planner",
    )

    assert first.provider == "gemini"
    assert first.content.strip()
    assert second.provider == "nvidia_api"
    assert second.content.strip()
    assert controller.provider_id == "nvidia_api"

    evidence = failover_store.list_recent(limit=20)
    assert any(row["phase"] == "failure_detected" for row in evidence)
    assert any(
        row["phase"] == "succeeded" and row["to_provider"] == "nvidia_api"
        for row in evidence
    )
