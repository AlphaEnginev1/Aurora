"""NVIDIA-hosted NIM provider adapter.

Increment 9A: preserve request/execution identity and emit provider-neutral
telemetry. Deeper transport/timeout/response hardening is handled in 9B.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from ..audit import now_utc
from ..errors import (
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransportError,
    ProviderErrorContext,
)
from ..models import (
    InferenceRequest,
    InferenceResponse,
    InferenceTelemetry,
    InferenceUsage,
    ModelSpec,
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
        if not execution_id:
            from ..models import new_execution_id
            execution_id = new_execution_id()
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
            ) from exc
        except (TimeoutError, socket_timeout_error()) as exc:
            raise ProviderTimeoutError(
                "NVIDIA inference request timed out",
                context=context,
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderTransportError(
                "NVIDIA inference transport failed",
                context=context,
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError(
                "NVIDIA inference response was not valid JSON",
                context=context,
            ) from exc

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                "NVIDIA response did not contain choices[0].message.content",
                context=context,
            ) from exc

        usage_raw = raw.get("usage") or {}
        usage = InferenceUsage(
            prompt_tokens=_as_int(usage_raw.get("prompt_tokens")),
            completion_tokens=_as_int(usage_raw.get("completion_tokens")),
            total_tokens=_as_int(usage_raw.get("total_tokens")),
        )
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
        reported_model = raw.get("model")
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
            reported_model=str(reported_model) if reported_model is not None else None,
            usage=usage,
            telemetry=telemetry,
        )


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def socket_timeout_error():
    return getattr(__import__("socket"), "timeout")
