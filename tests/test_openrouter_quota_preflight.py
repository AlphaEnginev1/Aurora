"""Unit tests for quota-safe OpenRouter live-inference preflight."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from aurora.inference.providers.openrouter_quota import (
    OpenRouterQuotaError,
    read_openrouter_free_quota,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def quota_payload(*, used=4, limit=50, remaining=46, is_free_tier=True):
    return {
        "data": {
            "is_free_tier": is_free_tier,
            "free_model_daily_requests": {
                "used": used,
                "limit": limit,
                "remaining": remaining,
            },
        }
    }


def test_quota_preflight_only_sends_authenticated_get_and_returns_allowance():
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(quota_payload())

    result = read_openrouter_free_quota(
        "fake-secret",
        urlopen=fake_urlopen,
    )

    assert result == {
        "used": 4,
        "limit": 50,
        "remaining": 46,
        "is_free_tier": True,
    }
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == "https://openrouter.ai/api/v1/key"
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer fake-secret"
    assert timeout == 15.0


def test_quota_preflight_fails_closed_before_any_generation_when_exhausted():
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.get_method())
        return FakeResponse(quota_payload(used=51, limit=50, remaining=0))

    with pytest.raises(OpenRouterQuotaError, match="allowance exhausted") as caught:
        read_openrouter_free_quota("fake-secret", urlopen=fake_urlopen)

    assert calls == ["GET"]
    assert "used=51" in str(caught.value)
    assert "remaining=0" in str(caught.value)
    assert "fake-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"is_free_tier": True}},
        {"data": {"is_free_tier": True, "free_model_daily_requests": {"used": 0, "limit": 50}}},
        {"data": {"is_free_tier": False, "free_model_daily_requests": {"used": 0, "limit": 50, "remaining": 50}}},
        {"data": {"is_free_tier": True, "free_model_daily_requests": {"used": 0, "limit": 50, "remaining": "unknown"}}},
    ],
)
def test_quota_preflight_fails_closed_when_allowance_cannot_be_proven(payload):
    with pytest.raises(OpenRouterQuotaError):
        read_openrouter_free_quota(
            "fake-secret",
            urlopen=lambda request, timeout: FakeResponse(payload),
        )


def test_quota_preflight_sanitizes_http_errors_without_reading_error_body():
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "unauthorized",
            {},
            io.BytesIO(b'{"error":"private-key-echo-must-not-escape"}'),
        )

    with pytest.raises(OpenRouterQuotaError, match="HTTP 401") as caught:
        read_openrouter_free_quota("fake-secret", urlopen=fake_urlopen)

    assert "fake-secret" not in str(caught.value)
    assert "private-key-echo" not in str(caught.value)
