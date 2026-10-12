"""OpenAI API provider adapter using Aurora's provider-neutral contract."""

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
from ..multimodal import MultimodalContentError, openai_messages


class OpenAIAPIProvider:
    """Execute OpenAI chat-completions requests without leaking provider concerns upstream."""

    provider_id = "openai"

    def __init__(
        self,
        spec: ModelSpec,
        api_key: str | None = None,
        timeout: float = 60.0,
        provider_id: str = "openai",
    ) -> None:
        self.spec = spec
        self.provider_id = str(provider_id).strip().lower() or "openai"
        self.api_key = api_key or (os.getenv("OPENROUTER_API_KEY") if self.provider_id == "openrouter" else os.getenv("OPENAI_API_KEY"))
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
            raise ProviderConfigurationError("OpenAI provider is not configured", context=context)
        try:
            messages = openai_messages(request.messages, allowed_modalities=frozenset({"text", "image"}))
        except MultimodalContentError as exc:
            raise ProviderResponseError(
                f"OpenAI request contains unsupported multimodal content: {exc}",
                context=context,
            ) from exc
        payload = {"model": request.model or self.spec.id, "messages": messages}
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
                f"OpenAI HTTP request failed with status {exc.code}",
                context=context,
                status_code=exc.code,
            ) from None
        except (TimeoutError, socket.timeout):
            raise ProviderTimeoutError("OpenAI inference request timed out", context=context) from None
        except urllib.error.URLError:
            raise ProviderTransportError("OpenAI inference transport failed", context=context) from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderResponseError("OpenAI inference response was not valid JSON", context=context) from None

        if not isinstance(raw, dict):
            raise ProviderResponseError("OpenAI response body must be a JSON object", context=context)
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderResponseError(
                "OpenAI response did not contain choices[0].message.content",
                context=context,
            ) from None
        if not isinstance(content, str):
            raise ProviderResponseError("OpenAI response content must be a string", context=context)
        usage = _usage(raw.get("usage"), context)
        reported_model = _reported_model(raw.get("model"), context)
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


def _usage(value: object, context: ProviderErrorContext) -> InferenceUsage:
    if value is None:
        return InferenceUsage()
    if not isinstance(value, dict):
        raise ProviderResponseError("OpenAI response usage must be an object", context=context)
    return InferenceUsage(
        prompt_tokens=_as_int(value.get("prompt_tokens")),
        completion_tokens=_as_int(value.get("completion_tokens")),
        total_tokens=_as_int(value.get("total_tokens")),
    )


def _reported_model(value: object, context: ProviderErrorContext) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError("OpenAI response model must be a non-empty string", context=context)
    return value


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


__all__ = ["OpenAIAPIProvider"]
