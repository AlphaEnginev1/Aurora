"""Opt-in LIVE acceptance for real OpenRouter free-router inference."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

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

    # OpenRouter exposes free-model daily usage for this key. This is a GET-only
    # preflight: it does not consume a generation request and never prints the key.
    quota_request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(quota_request, timeout=15) as quota_response:
            quota_payload = json.loads(quota_response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            f"OpenRouter key/quota preflight failed with HTTP {exc.code}; key was not printed"
        ) from None
    except (TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AssertionError("OpenRouter key/quota preflight could not be completed safely") from None
    quota_data = quota_payload.get("data", {}) if isinstance(quota_payload, dict) else {}
    free_quota = quota_data.get("free_model_daily_requests", {}) if isinstance(quota_data, dict) else {}
    if isinstance(free_quota, dict):
        print(
            "AURORA_OPENROUTER_QUOTA "
            f"used={free_quota.get('used')} limit={free_quota.get('limit')} "
            f"remaining={free_quota.get('remaining')} "
            f"is_free_tier={quota_data.get('is_free_tier')}"
        )
    else:
        print("AURORA_OPENROUTER_QUOTA status=quota_fields_unavailable")

    # Check the authenticated model-list endpoint as connection evidence before
    # considering a generation. A free quota exhaustion is a provider/account
    # state, not a broken base URL, and should not generate futile chat requests.
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
            f"OpenRouter authenticated model-list check failed with HTTP {exc.code}; key was not printed"
        ) from None
    except (TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AssertionError("OpenRouter authenticated model-list check could not be completed safely") from None
    model_rows = models_payload.get("data") if isinstance(models_payload, dict) else None
    if not isinstance(model_rows, list):
        raise AssertionError("OpenRouter authenticated model-list response was not a data list")
    print(f"AURORA_OPENROUTER_CONNECTION status=success listed_models={len(model_rows)}")

    if isinstance(free_quota, dict):
        remaining = free_quota.get("remaining")
        try:
            quota_exhausted = remaining is not None and float(remaining) <= 0
        except (TypeError, ValueError):
            quota_exhausted = False
        if quota_exhausted:
            raise AssertionError(
                "OpenRouter connection is authenticated but free inference is blocked: "
                f"daily allowance exhausted (used={free_quota.get('used')}, "
                f"limit={free_quota.get('limit')}, remaining={remaining}). "
                "No chat request was sent; wait for quota reset or use explicitly authorized allowance."
            )

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
