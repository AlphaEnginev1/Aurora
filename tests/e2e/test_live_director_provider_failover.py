from __future__ import annotations

import os

import pytest

from aurora.inference.discovery import ModelCandidateStore, ModelDiscoveryService
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter
from aurora.operator_settings import OperatorSettingsStore
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.director_provider_failover import (
    DirectorProviderFailoverController,
    DirectorProviderFailoverStore,
)
from aurora.orchestration.factory import _build_director_provider_failover
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
def test_live_gemini_to_dynamically_selected_nvidia_provider_failover(tmp_path):
    if os.getenv("AURORA_DIRECTOR_PROVIDER_FAILOVER_LIVE") != "1":
        pytest.skip("live provider failover gate is opt-in")

    gemini_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL_ID", "gemini-3.7-flash")
    nvidia_key = os.getenv("NVIDIA_API_KEY")
    if not gemini_key:
        raise AssertionError("live provider failover gate requires GEMINI_API_KEY")
    if not nvidia_key:
        raise AssertionError("live provider failover gate requires NVIDIA_API_KEY")

    settings = OperatorSettingsStore(tmp_path / "operator-settings.json")
    settings.save({
        "director": {
            "provider": "gemini",
            "model": gemini_model,
            "endpoint": os.getenv(
                "GEMINI_API_ENDPOINT",
                "https://generativelanguage.googleapis.com/v1beta",
            ),
            "failover": {"enabled": True, "order": ["nvidia_api", "router"]},
        },
        "providers": {
            "gemini": {"api_key": gemini_key},
            "nvidia_api": {
                "api_key": nvidia_key,
                "base_url": os.getenv(
                    "NVIDIA_API_ENDPOINT",
                    "https://integrate.api.nvidia.com/v1",
                ),
            },
        },
    })

    provider_registry = default_provider_registry(nvidia_api_key=nvidia_key)
    model_registry = ModelRegistry.from_specs([], provider_registry=provider_registry)
    router = ModelRouter(
        models=[],
        nvidia_api_key=nvidia_key,
        provider_registry=provider_registry,
        model_registry=model_registry,
    )
    candidate_store = ModelCandidateStore(tmp_path / "model-candidates.db")
    discovery = ModelDiscoveryService(
        provider_registry,
        model_registry,
        candidate_store,
    )

    discovered = discovery.discover_and_stage("nvidia_api")
    assert discovered, "NVIDIA /v1/models returned no models"
    assert any("reasoning" in item.model.capabilities for item in discovered), (
        "NVIDIA catalog did not yield a conversational reasoning candidate"
    )

    gemini = OneShotGeminiFailureProxy(GeminiDirectorProvider(
        api_key=gemini_key,
        model=gemini_model,
        endpoint=os.getenv(
            "GEMINI_API_ENDPOINT",
            "https://generativelanguage.googleapis.com/v1beta",
        ),
    ))
    controller = _build_director_provider_failover(
        settings_store=settings,
        router=router,
        active_provider=gemini,
        db_path=tmp_path / "tasks.db",
    )

    messages = [
        {
            "role": "system",
            "content": "You are Aurora Director. Respond with one short sentence.",
        },
        {
            "role": "user",
            "content": "Confirm that the live dynamic provider failover gate is running.",
        },
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
    assert second.model
    assert controller.provider_id == "nvidia_api"

    evidence = controller._store.list_recent(limit=50)
    assert any(row["phase"] == "failure_detected" for row in evidence)
    assert any(
        row["phase"] == "succeeded" and row["to_provider"] == "nvidia_api"
        for row in evidence
    )
