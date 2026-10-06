"""LIVE acceptance for Aurora's NVIDIA-only free-model inference fabric."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aurora.inference.discovery import ModelCandidateStore, ModelDiscoveryService
from aurora.inference.models import InferenceRequest
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.registry import ModelRegistry
from aurora.inference.router import ModelRouter


@pytest.mark.live
def test_live_nvidia_only_catalog_and_inference(tmp_path: Path):
    if os.getenv("AURORA_NVIDIA_ONLY_LIVE") != "1":
        pytest.skip("live NVIDIA gate is opt-in")
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not key:
        raise AssertionError("LIVE-NVIDIA-only gate requires NVIDIA_API_KEY")

    registry = default_provider_registry(nvidia_api_key=key)
    assert registry.ids == ("nvidia_api",)

    model_registry = ModelRegistry.from_file(
        "config/inference-policy.json",
        provider_registry=registry,
    )
    router = ModelRouter(
        models=list(model_registry.models),
        nvidia_api_key=key,
        provider_registry=registry,
        model_registry=model_registry,
        cost_policy="free_only",
    )

    candidates = ModelCandidateStore(tmp_path / "model-candidates.db")
    discovery = ModelDiscoveryService(registry, model_registry, candidates)
    discovered = tuple(discovery.discover_and_stage("nvidia_api"))
    free_models = tuple(
        item.model
        for item in discovered
        if item.model.pricing_tier == "free"
    )
    assert free_models, "NVIDIA account exposed no Free Endpoint models"
    assert all(item.provider == "nvidia_api" for item in free_models)

    response = router.generate(
        InferenceRequest(
            messages=[
                {
                    "role": "user",
                    "content": "Respond with exactly one short sentence confirming NVIDIA inference.",
                }
            ],
            metadata={"allowed_models": [item.id for item in free_models]},
        ),
        capability="reasoning",
        preferred_models=tuple(item.id for item in free_models),
    )
    assert response.provider == "nvidia_api"
    assert response.model
    assert response.content.strip()
