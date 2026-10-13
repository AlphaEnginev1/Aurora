"""Opt-in LIVE acceptance for real OpenRouter free-router inference."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

from aurora.inference.providers.openrouter_quota import (
    OpenRouterQuotaError,
    read_openrouter_free_quota,
)

from aurora.inference.models import InferenceRequest
from aurora.orchestration.factory import build_orchestration_engine


@pytest.mark.live
def test_live_openrouter_free_router_inference():
    if os.getenv("AURORA_OPENROUTER_LIVE") != "1":
        pytest.skip("live OpenRouter gate is opt-in")
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise AssertionError("LIVE OpenRouter gate requires OPENROUTER_API_KEY")

    # This authenticated GET does not consume a chat-completion request. It
    # fails closed if the free allowance is exhausted or its fields are unknown.
    try:
        quota = read_openrouter_free_quota(key)
    except OpenRouterQuotaError as exc:
        raise AssertionError(str(exc)) from None
    print(
        "AURORA_OPENROUTER_QUOTA "
        f"used={quota['used']} limit={quota['limit']} "
        f"remaining={quota['remaining']} is_free_tier={quota['is_free_tier']}"
    )

    # Confirm authenticated endpoint connectivity with a GET before spending
    # one permitted free generation.
    models_request = urllib.request.Request(
        "https://openrouter.ai/api/v1/models?limit=10",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(models_request, timeout=15) as models_response:
            models_payload = json.loads(models_response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            f"OpenRouter authenticated model-list check failed with HTTP {exc.code}; key was not logged"
        ) from None
    except (TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AssertionError("OpenRouter model-list check could not be completed safely") from None
    model_rows = models_payload.get("data") if isinstance(models_payload, dict) else None
    if not isinstance(model_rows, list):
        raise AssertionError("OpenRouter model-list response was invalid; refusing generation")
    print(f"AURORA_OPENROUTER_CONNECTION status=success listed_models={len(model_rows)}")

    engine = build_orchestration_engine("config/inference-policy.json")
    response = engine._router.generate(
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
