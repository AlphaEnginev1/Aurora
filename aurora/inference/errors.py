"""Typed failures and safe diagnostics for provider execution boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import time
from typing import Any


@dataclass(frozen=True)
class ProviderErrorContext:
    provider: str
    request_id: str
    execution_id: str
    requested_model: str | None = None
    selected_model: str | None = None
    provider_model: str | None = None


class ProviderExecutionError(RuntimeError):
    """Base error for a failed provider execution."""

    code = "provider_execution_error"
    retryable = False

    def __init__(self, message: str, *, context: ProviderErrorContext) -> None:
        super().__init__(message)
        self.context = context


class ProviderConfigurationError(ProviderExecutionError):
    code = "provider_configuration_error"
    retryable = False


class ProviderTimeoutError(ProviderExecutionError):
    code = "provider_timeout"
    retryable = True


class ProviderTransportError(ProviderExecutionError):
    code = "provider_transport_error"
    retryable = True


class ProviderResponseError(ProviderExecutionError):
    code = "provider_invalid_response"
    retryable = False


def provider_http_diagnostics(error: Any) -> dict[str, Any]:
    """Extract only allowlisted, secret-free diagnostics from an HTTPError.

    Provider response bodies are never returned or logged verbatim. Only a safe
    provider error code, a normalized reason category, and bounded Retry-After
    metadata may cross this boundary.
    """
    status = int(getattr(error, "code", 0) or 0)
    headers = getattr(error, "headers", None)
    retry_after: float | None = None
    raw_retry = None
    if headers is not None:
        try:
            raw_retry = headers.get("Retry-After") or headers.get("retry-after")
        except AttributeError:
            raw_retry = None
    if raw_retry:
        try:
            retry_after = max(0.0, min(86400.0, float(str(raw_retry).strip())))
        except (TypeError, ValueError):
            try:
                retry_date = parsedate_to_datetime(str(raw_retry))
                if retry_date.tzinfo is None:
                    retry_date = retry_date.replace(tzinfo=timezone.utc)
                retry_after = max(
                    0.0,
                    min(86400.0, (retry_date - datetime.now(timezone.utc)).total_seconds()),
                )
            except (TypeError, ValueError, OverflowError):
                retry_after = None
    if retry_after is not None:
        retry_after = round(retry_after, 3)

    try:
        raw_body = error.read(8192)
    except Exception:
        raw_body = b""
    if isinstance(raw_body, bytes):
        body_text = raw_body.decode("utf-8", errors="replace")
    else:
        body_text = str(raw_body or "")
    try:
        payload = json.loads(body_text) if body_text else {}
    except (TypeError, ValueError):
        payload = {}
    payload_error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(payload_error, dict):
        payload_error = {}

    raw_code = payload_error.get("code") or payload_error.get("type")
    provider_code = None
    if isinstance(raw_code, (str, int)) and not isinstance(raw_code, bool):
        candidate = str(raw_code).strip()
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", candidate):
            provider_code = candidate

    provider_text = " ".join(
        str(payload_error.get(key, ""))
        for key in ("code", "type", "message")
        if payload_error.get(key) is not None
    ).lower()
    if status == 401:
        reason = "authentication_rejected"
    elif status == 403:
        reason = "access_denied_or_model_entitlement"
    elif status == 404:
        reason = "endpoint_or_model_not_found"
    elif status == 429:
        if ("free-model" in provider_text or "free model" in provider_text) and any(
            token in provider_text for token in ("daily", "per-day", "rpd", "limit")
        ):
            reason = "free_tier_limit"
        elif any(token in provider_text for token in ("quota", "credit", "billing limit")):
            reason = "quota_or_account_limit"
        else:
            reason = "rate_limit_or_temporary_capacity"
    elif status in {408, 500, 502, 503, 504}:
        reason = "provider_temporary_unavailable"
    else:
        reason = "provider_http_error"

    return {
        "failure_reason": reason,
        "provider_code": provider_code,
        "retry_after_seconds": retry_after,
        "retryable": status in {408, 409, 425, 429, 500, 502, 503, 504},
    }


class ProviderHTTPError(ProviderExecutionError):
    code = "provider_http_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        context: ProviderErrorContext,
        status_code: int | None = None,
        provider_code: str | None = None,
        failure_reason: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.status_code = status_code
        self.provider_code = provider_code
        self.failure_reason = failure_reason
        self.retry_after_seconds = retry_after_seconds
        self.retryable = bool(
            status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        )
