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
        f"discovered={len(discovered)} activated={len(activated)} "
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

    # Reserve-pool acceptance: first establish real NVIDIA inference above, then
    # force one controlled primary-model failure at the provider boundary and
    # require the same router to obtain a real response from a different,
    # currently catalog-approved NVIDIA Free Endpoint model. This is a controlled
    # recovery drill; the injected 503 is not represented as a real upstream error.
    from aurora.inference.errors import ProviderErrorContext, ProviderHTTPError

    reserve_candidates = tuple(model_id for model_id in candidate_ids if model_id != response.model)
    assert reserve_candidates, "NVIDIA live catalog must expose at least one reserve model"

    primary_model = response.model
    original_create = registry.create
    injected_attempts = []

    class InjectedPrimaryFailure:
        provider_id = "nvidia_api"

        def __init__(self, spec):
            self.spec = spec

        def generate(self, request, *, selected_model=None, execution_id=""):
            injected_attempts.append(selected_model)
            raise ProviderHTTPError(
                "controlled NVIDIA reserve acceptance failure",
                context=ProviderErrorContext(
                    provider="nvidia_api",
                    request_id=request.request_id,
                    execution_id=execution_id,
                    requested_model=request.model,
                    selected_model=selected_model,
                    provider_model=self.spec.id,
                ),
                status_code=503,
            )

    def create_with_controlled_primary_failure(spec):
        if spec.id == primary_model:
            return InjectedPrimaryFailure(spec)
        return original_create(spec)

    # Put the proven primary first so the first route decision is deterministic.
    reserve_preference = (primary_model, *reserve_candidates)
    registry.create = create_with_controlled_primary_failure
    try:
        reserve_response = router.generate(
            InferenceRequest(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Respond with exactly one short sentence confirming "
                            "NVIDIA reserve-model inference."
                        ),
                    }
                ],
                metadata={"allowed_models": list(candidate_ids)},
            ),
            capability="reasoning",
            preferred_models=reserve_preference,
        )
    finally:
        registry.create = original_create

    assert injected_attempts == [primary_model]
    assert reserve_response.provider == "nvidia_api"
    assert reserve_response.model in set(reserve_candidates)
    assert reserve_response.model != primary_model
    assert reserve_response.content.strip()
    assert reserve_response.telemetry is not None
    assert reserve_response.telemetry.success is True
    print(
        "AURORA_NVIDIA_RESERVE_LIVE_RESULT "
        f"status=success primary_model={primary_model} "
        "primary_failure=controlled_injected_http_503 "
        f"reserve_model={reserve_response.model} "
        f"request_id={reserve_response.request_id} "
        f"execution_id={reserve_response.execution_id}"
    )
