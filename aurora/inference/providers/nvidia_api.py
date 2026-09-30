"""NVIDIA-hosted NIM provider adapter.

Increment 9B hardens the real provider boundary: response validation,
usage/latency capture, typed transport failures, model attribution, and
credential-safe error handling.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

from ..audit import now_utc
from ..errors import (
    ProviderConfigurationError,
    ProviderErrorContext,
    ProviderExecutionError,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from ..models import (
    InferenceRequest,
    InferenceResponse,
    InferenceTelemetry,
    InferenceUsage,
    ModelSpec,
    new_execution_id,
)


class NVIDIAAPIError(ProviderExecutionError):
    """Backward-compatible NVIDIA provider error alias."""


class NVIDIAAPIProvider:
    provider_id = "nvidia_api"

    def __init__(
        self,
        spec: ModelSpec,
        api_key: str | None = None,
        timeout: float = 60.0,
    ):
        self.spec = spec
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY")
        self.timeout = timeout

    def health(self) -> bool:
        return bool(self.spec.enabled and self.spec.endpoint and self.api_key)

    def generate(
        self,
        request: InferenceRequest,
        *,
        selected_model: str | None = None,
        execution_id: str = "",
    ) -> InferenceResponse:
        selected = selected_model or self.spec.id
        execution_id = execution_id or new_execution_id()
        started_at = now_utc()
        started = time.monotonic()
        context = ProviderErrorContext(
            provider=self.provider_id,
            request_id=request.request_id,
            execution_id=execution_id,
            requested_model=request.model,
            selected_model=selected,
            provider_model=self.spec.id,
        )

        if not self.health():
            raise ProviderConfigurationError(
                "NVIDIA provider is not configured",
                context=context,
            )

        payload = {
            "model": request.model or self.spec.id,
            "messages": request.messages,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.provider_options:
            payload.update(request.provider_options)

        req = urllib.request.Request(
            self.spec.endpoint.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderHTTPError(
                f"NVIDIA HTTP request failed with status {exc.code}",
                context=context,
            ) from None
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError(
                "NVIDIA inference request timed out",
                context=context,
            ) from None
        except urllib.error.URLError as exc:
            raise ProviderTransportError(
                "NVIDIA inference transport failed",
                context=context,
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError(
                "NVIDIA inference response was not valid JSON",
                context=context,
            ) from None

        content = _extract_content(raw, context)
        usage = _extract_usage(raw, context)
        reported_model = _extract_reported_model(raw, context)
        duration_ms = int((time.monotonic() - started) * 1000)
        telemetry = InferenceTelemetry(
            request_id=request.request_id,
            execution_id=execution_id,
            provider=self.provider_id,
            model=self.spec.id,
            started_at=started_at,
            duration_ms=duration_ms,
            success=True,
        )
        return InferenceResponse(
            model=self.spec.id,
            provider=self.provider_id,
            content=content,
            raw=raw,
            request_id=request.request_id,
            execution_id=execution_id,
            requested_model=request.model,
            selected_model=selected,
            provider_model=self.spec.id,
            reported_model=reported_model,
            usage=usage,
            telemetry=telemetry,
        )


def _response_error(message: str, context: ProviderErrorContext) -> ProviderResponseError:
    return ProviderResponseError(message, context=context)


def _extract_content(raw: object, context: ProviderErrorContext) -> str:
    if not isinstance(raw, dict):
        raise _response_error("NVIDIA response body must be a JSON object", context)
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise _response_error(
            "NVIDIA response did not contain choices[0].message.content",
            context,
        ) from None
    if not isinstance(content, str):
        raise _response_error(
            "NVIDIA response content must be a string",
            context,
        )
    return content


def _extract_usage(raw: object, context: ProviderErrorContext) -> InferenceUsage:
    if not isinstance(raw, dict):
        raise _response_error("NVIDIA response body must be a JSON object", context)
    usage_raw = raw.get("usage")
    if usage_raw is None:
        return InferenceUsage()
    if not isinstance(usage_raw, dict):
        raise _response_error("NVIDIA response usage must be an object", context)
    return InferenceUsage(
        prompt_tokens=_as_int(usage_raw.get("prompt_tokens")),
        completion_tokens=_as_int(usage_raw.get("completion_tokens")),
        total_tokens=_as_int(usage_raw.get("total_tokens")),
    )


def _extract_reported_model(raw: object, context: ProviderErrorContext) -> str | None:
    if not isinstance(raw, dict):
        raise _response_error("NVIDIA response body must be a JSON object", context)
    reported = raw.get("model")
    if reported is None:
        return None
    if not isinstance(reported, str) or not reported.strip():
        raise _response_error("NVIDIA response model must be a non-empty string", context)
    return reported


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
