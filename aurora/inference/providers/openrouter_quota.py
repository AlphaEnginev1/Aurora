"""Fail-closed quota preflight for OpenRouter's free-model daily allowance."""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


class OpenRouterQuotaError(RuntimeError):
    """Raised when it is unsafe to attempt a free OpenRouter generation."""


def read_openrouter_free_quota(
    api_key: str,
    *,
    urlopen: Callable[..., Any] | None = None,
    timeout: float = 15.0,
) -> dict[str, int | float | bool]:
    """Read authenticated key allowance without making a chat-completion request.

    The function fails closed: absent or malformed quota metadata must never be
    interpreted as permission to send a generation request.
    """
    key = str(api_key or "").strip()
    if not key:
        raise OpenRouterQuotaError("OpenRouter quota preflight requires a configured API key")

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    opener = urlopen or urllib.request.urlopen
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OpenRouterQuotaError(
            f"OpenRouter quota preflight failed with HTTP {exc.code}; key was not logged"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise OpenRouterQuotaError(
            "OpenRouter quota preflight could not be completed; refusing to send a generation request"
        ) from None

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise OpenRouterQuotaError(
            "OpenRouter quota response did not contain recognized account data; refusing generation"
        )

    data = payload["data"]
    if data.get("is_free_tier") is not True:
        raise OpenRouterQuotaError(
            "OpenRouter account could not be verified as free-tier; refusing generation under free-only policy"
        )
    quota = data.get("free_model_daily_requests")
    if not isinstance(quota, dict):
        raise OpenRouterQuotaError(
            "OpenRouter free-model daily quota fields are unavailable; refusing generation"
        )

    values: dict[str, int | float] = {}
    for field in ("used", "limit", "remaining"):
        value = quota.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OpenRouterQuotaError(
                f"OpenRouter free-model quota field '{field}' is missing or invalid; refusing generation"
            )
        if not math.isfinite(float(value)) or value < 0:
            raise OpenRouterQuotaError(
                f"OpenRouter free-model quota field '{field}' is invalid; refusing generation"
            )
        values[field] = value

    if values["limit"] <= 0:
        raise OpenRouterQuotaError(
            "OpenRouter free-model daily limit is not positive; refusing generation"
        )
    if values["remaining"] <= 0:
        raise OpenRouterQuotaError(
            "OpenRouter free-model daily allowance exhausted "
            f"(used={values['used']}, limit={values['limit']}, remaining={values['remaining']}); "
            "no chat-completion request was sent"
        )

    return {
        "used": values["used"],
        "limit": values["limit"],
        "remaining": values["remaining"],
        "is_free_tier": True,
    }


__all__ = ["OpenRouterQuotaError", "read_openrouter_free_quota"]
