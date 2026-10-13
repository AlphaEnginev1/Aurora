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

    endpoint = os.getenv(
        "NVIDIA_API_ENDPOINT", "https://integrate.api.nvidia.com/v1"
    ).strip().rstrip("/") or "https://integrate.api.nvidia.com/v1"
    registry = default_provider_registry(
        nvidia_api_key=key,
        provider_endpoints={"nvidia_api": endpoint},
    )
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
    assert discovered, "NVIDIA official catalog discovery returned no models"
    assert all(item.provider_id == "nvidia_api" for item in discovered)

    # Governance is the source of truth: use every currently discovered Free
    # Endpoint model it approves, not the seed registry or a static shortlist.
    activated = discovery.activate_free_discovered(provider_id="nvidia_api")
    reasoning_models = tuple(
        model
        for model in activated
        if model.provider == "nvidia_api"
        and model.enabled
        and model.pricing_tier == "free"
        and "reasoning" in model.capabilities
    )
    assert reasoning_models, (
        "Official NVIDIA Free Endpoint catalog exposed no governance-approved "
        "enabled free model with reasoning capability; "
        f"discovered={len(discovered)} activated={len(activated)}"
    )
    candidate_ids = tuple(model.id for model in reasoning_models)
    requested_model = os.getenv("NVIDIA_MODEL_ID", "").strip()
    preferred = tuple(
        dict.fromkeys(
            ([requested_model] if requested_model in candidate_ids else [])
            + list(candidate_ids)
        )
    )
    if requested_model and requested_model not in candidate_ids:
        print(
            "AURORA_NVIDIA_MODEL_PREFERENCE "
            f"requested={requested_model!r} status=ignored_not_in_governed_reasoning_pool"
        )
    print(
        "AURORA_NVIDIA_CATALOG "
        f"endpoint={endpoint} discovered={len(discovered)} activated={len(activated)} "
        f"reasoning_free_candidates={len(candidate_ids)} models={list(candidate_ids)!r}"
    )

    response = router.generate(
        InferenceRequest(
            messages=[
                {
                    "role": "user",
                    "content": "Respond with exactly one short sentence confirming NVIDIA inference.",
                }
            ],
            # The allowlist includes the whole governed pool. NVIDIA_MODEL_ID is
            # only a preference and must never remove otherwise eligible reserves.
            metadata={"allowed_models": list(candidate_ids)},
        ),
        capability="reasoning",
        preferred_models=preferred,
    )
    assert response.provider == "nvidia_api"
    assert response.model in set(candidate_ids)
    assert response.content.strip()
    print(
        "AURORA_NVIDIA_LIVE_RESULT "
        f"status=success provider={response.provider} selected_model={response.model} "
        f"request_id={response.request_id} execution_id={response.execution_id}"
    )
