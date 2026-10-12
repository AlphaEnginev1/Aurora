"""Opt-in LIVE acceptance for real OpenRouter free-router inference."""

from __future__ import annotations

import os

import pytest

from aurora.inference.models import InferenceRequest
from aurora.orchestration.factory import build_orchestration_engine


@pytest.mark.live
def test_live_openrouter_free_router_inference():
    if os.getenv("AURORA_OPENROUTER_LIVE") != "1":
        pytest.skip("live OpenRouter gate is opt-in")
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise AssertionError("LIVE OpenRouter gate requires OPENROUTER_API_KEY")

    engine = build_orchestration_engine("config/inference-policy.json")
    response = engine.router.generate(
        InferenceRequest(
            messages=[{
                "role": "user",
                "content": "Reply with exactly: AURORA OPENROUTER LIVE OK",
            }],
            model="openrouter/free",
        ),
        capability="reasoning",
        preferred_models=("openrouter/free",),
    )

    assert response.provider == "openrouter"
    assert response.model == "openrouter/free"
    assert response.content.strip()
    assert response.telemetry is not None
    assert response.telemetry.success is True
    print(
        "AURORA_OPENROUTER_LIVE_RESULT "
        f"status=success provider={response.provider} model={response.model} "
        f"reported_model={response.reported_model} request_id={response.request_id} "
        f"execution_id={response.execution_id}"
    )
