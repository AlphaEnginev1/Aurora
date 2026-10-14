"""Anthropic Messages API provider adapter."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

from aurora.autonomy.internet import InternetPolicyError
from typing import Any

from ..audit import now_utc
from ..errors import (
    ProviderConfigurationError,
    ProviderErrorContext,
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
from ..multimodal import MultimodalContentError, message_modalities


class AnthropicAPIProvider:
    """Execute Anthropic Messages API requests behind Aurora's common contract."""

    provider_id = "anthropic"

    def __init__(
        self,
        spec: ModelSpec,
        api_key: str | None = None,
        timeout: float = 60.0,
        api_version: str = "2023-06-01",
        gateway=None,
    ) -> None:
        self.spec = spec
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.timeout = timeout
        self.api_version = api_version
        self.gateway = gateway

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
            raise ProviderConfigurationError("Anthropic provider is not configured", context=context)

        try:
            modalities = message_modalities(request.messages)
            messages, system = _anthropic_messages(request.messages)
        except MultimodalContentError as exc:
            raise ProviderResponseError(
                f"Anthropic request contains unsupported multimodal content: {exc}",
                context=context,
            ) from exc

        payload: dict[str, Any] = {
            "model": request.model or self.spec.id,
            "messages": messages,
            "max_tokens": request.max_tokens if request.max_tokens is not None else 1024,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        url = self.spec.endpoint.rstrip("/") + "/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps(payload).encode("utf-8")
        try:
            if self.gateway is not None:
                response = self.gateway.request_with_metadata(url, method="POST", headers=headers, body=body)
                response_body = response.body
            else:
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    response_body = response.read()
            raw = json.loads(response_body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderHTTPError(
                f"Anthropic HTTP request failed with status {exc.code}",
                context=context,
                status_code=exc.code,
            ) from None
        except InternetPolicyError as exc:
            cause = exc.__cause__
            if isinstance(cause, urllib.error.HTTPError):
                raise ProviderHTTPError(
                    f"Anthropic HTTP request failed with status {cause.code}",
                    context=context,
                    status_code=cause.code,
                ) from None
            raise ProviderTransportError("Anthropic inference transport failed", context=context) from None
        except (TimeoutError, socket.timeout):
            raise ProviderTimeoutError("Anthropic inference request timed out", context=context) from None
        except urllib.error.URLError:
            raise ProviderTransportError("Anthropic inference transport failed", context=context) from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderResponseError("Anthropic inference response was not valid JSON", context=context) from None

        if not isinstance(raw, dict):
            raise ProviderResponseError("Anthropic response body must be a JSON object", context=context)
        content = _extract_text(raw.get("content"), context)
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


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    converted: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for message in messages:
        role = str(message.get("role", "")).strip().lower()
        content = message.get("content", "")
        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                system_parts.extend(
                    str(part.get("text", "")).strip()
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text", "")).strip()
                )
            continue
        if role not in {"user", "assistant"}:
            raise MultimodalContentError(f"Anthropic does not accept message role {role!r}")
        converted.append({"role": role, "content": _content_parts(content)})
    return converted, "\n\n".join(part for part in system_parts if part)


def _content_parts(content: object) -> object:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise MultimodalContentError("message content must be text or a list of content parts")
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            raise MultimodalContentError("content parts must be objects")
        modality = str(part.get("modality", part.get("type", ""))).strip().lower()
        if modality == "text":
            parts.append({"type": "text", "text": str(part.get("text", ""))})
        elif modality == "image":
            mime_type = str(part.get("mime_type", "")).strip()
            data = str(part.get("data", "")).strip()
            if not mime_type or not data:
                raise MultimodalContentError("image parts require mime_type and data")
            media_type = mime_type
            parts.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": data},
            })
        else:
            raise MultimodalContentError(f"Anthropic adapter does not support modality {modality!r}")
    return parts


def _extract_text(value: object, context: ProviderErrorContext) -> str:
    if not isinstance(value, list):
        raise ProviderResponseError("Anthropic response content must be a list", context=context)
    texts = [
        str(item.get("text"))
        for item in value
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
    ]
    if not texts:
        raise ProviderResponseError("Anthropic response did not contain text content", context=context)
    return "".join(texts)


def _usage(value: object, context: ProviderErrorContext) -> InferenceUsage:
    if value is None:
        return InferenceUsage()
    if not isinstance(value, dict):
        raise ProviderResponseError("Anthropic response usage must be an object", context=context)
    return InferenceUsage(
        prompt_tokens=_as_int(value.get("input_tokens")),
        completion_tokens=_as_int(value.get("output_tokens")),
        total_tokens=(
            _as_int(value.get("input_tokens")) or 0
        ) + (_as_int(value.get("output_tokens")) or 0)
        if value.get("input_tokens") is not None or value.get("output_tokens") is not None
        else None,
    )


def _reported_model(value: object, context: ProviderErrorContext) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderResponseError("Anthropic response model must be a non-empty string", context=context)
    return value


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


__all__ = ["AnthropicAPIProvider"]
