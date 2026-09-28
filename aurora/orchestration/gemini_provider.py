"""Gemini-backed Director provider.

This adapter translates Gemini function calls into Aurora's provider-neutral
DirectorToolCall contract. It never executes tools.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from .director_provider import DirectorModelResponse, DirectorProvider, DirectorToolSpec, DirectorToolCall


class GeminiDirectorError(RuntimeError):
    """Raised when Gemini cannot produce a valid Director response."""


@dataclass(frozen=True)
class GeminiDirectorConfig:
    api_key: str
    model: str = "gemini-3.7-flash"
    endpoint: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 30.0
    max_retries: int = 2


class GeminiDirectorProvider:
    """Provider adapter for Gemini function calling without vendor leakage."""

    provider_id = "gemini"

    def __init__(
        self,
        config: GeminiDirectorConfig | None = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self.config = config or GeminiDirectorConfig(
            api_key=api_key or os.getenv("GEMINI_API_KEY", ""),
            model=model or os.getenv("GEMINI_MODEL_ID", "gemini-3.7-flash"),
            endpoint=endpoint or os.getenv(
                "GEMINI_API_ENDPOINT",
                "https://generativelanguage.googleapis.com/v1beta",
            ),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        if not self.config.api_key.strip():
            raise GeminiDirectorError("GEMINI_API_KEY is not configured")
        if not self.config.model.strip():
            raise GeminiDirectorError("Gemini model is empty")
        if self.config.timeout_seconds <= 0:
            raise GeminiDirectorError("Gemini timeout must be positive")
        if self.config.max_retries < 0:
            raise GeminiDirectorError("Gemini max_retries must be non-negative")

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
        tools: tuple[DirectorToolSpec, ...] = (),
    ) -> DirectorModelResponse:
        selected_model = (model or self.config.model).strip()
        system_instruction, contents = self._translate_messages(messages)
        body: dict[str, Any] = {"contents": contents}
        if system_instruction:
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction}],
            }
        if tools:
            body["tools"] = [{
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": self._gemini_schema(tool.parameters),
                    }
                    for tool in tools
                ],
            }]
        body["generationConfig"] = {"maxOutputTokens": 3000}

        request_id = uuid4().hex
        started = monotonic()
        endpoint = f"{self.config.endpoint.rstrip('/')}/models/{selected_model}:generateContent"
        request = Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.config.api_key,
            },
            method="POST",
        )
        payload = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.config.max_retries:
                    raise GeminiDirectorError(
                        f"Gemini HTTP {exc.code}: {detail}"
                    ) from exc
                sleep(0.5 * (2**attempt))
            except (URLError, TimeoutError, OSError) as exc:
                if attempt >= self.config.max_retries:
                    raise GeminiDirectorError(f"Gemini transport failure: {exc}") from exc
                sleep(0.5 * (2**attempt))
            except json.JSONDecodeError as exc:
                raise GeminiDirectorError("Gemini returned invalid JSON") from exc
        if payload is None:
            raise GeminiDirectorError("Gemini returned no response payload")

        content, tool_calls = self._parse_response(payload)
        return DirectorModelResponse(
            content=content,
            model=selected_model,
            provider=self.provider_id,
            request_id=request_id,
            execution_id=uuid4().hex,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Translate Aurora's JSON Schema subset to Gemini's schema shape."""
        if not isinstance(schema, dict):
            raise GeminiDirectorError("Gemini tool parameters must be an object")

        translated: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "additionalProperties":
                # Gemini's generateContent schema does not accept this JSON
                # Schema keyword even when it is set to false.
                continue
            if key == "type" and isinstance(value, list):
                non_null = [item for item in value if item != "null"]
                if len(value) == 2 and len(non_null) == 1 and "null" in value:
                    translated["type"] = non_null[0]
                    translated["nullable"] = True
                    continue
                raise GeminiDirectorError("Gemini schema type unions must be string|null")
            if key == "properties" and isinstance(value, dict):
                translated[key] = {
                    name: GeminiDirectorProvider._gemini_schema(prop)
                    for name, prop in value.items()
                    if isinstance(prop, dict)
                }
                continue
            if key == "items" and isinstance(value, dict):
                translated[key] = GeminiDirectorProvider._gemini_schema(value)
                continue
            translated[key] = value
        return translated

    @staticmethod
    def _translate_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if role == "system":
                system_parts.append(content)
                continue
            mapped_role = "model" if role in {"assistant", "model"} else "user"
            contents.append({
                "role": mapped_role,
                "parts": [{"text": content}],
            })
        if not contents:
            raise GeminiDirectorError("Gemini request contains no user/model content")
        return "\n\n".join(system_parts), contents

    @staticmethod
    def _parse_response(payload: dict[str, Any]) -> tuple[str, tuple[DirectorToolCall, ...]]:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiDirectorError("Gemini response contains no candidates")
        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", []) if isinstance(content_obj, dict) else []
        if not isinstance(parts, list):
            raise GeminiDirectorError("Gemini response parts are invalid")

        text_parts: list[str] = []
        calls: list[DirectorToolCall] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                name = function_call.get("name")
                args = function_call.get("args", {})
                call_id = function_call.get("id") or uuid4().hex
                if not isinstance(name, str) or not name.strip():
                    raise GeminiDirectorError("Gemini returned a function call without a name")
                if not isinstance(args, dict):
                    raise GeminiDirectorError("Gemini function call arguments are not an object")
                calls.append(
                    DirectorToolCall(
                        call_id=str(call_id),
                        name=name,
                        arguments=args,
                    )
                )
        if not text_parts and not calls:
            raise GeminiDirectorError("Gemini response contains neither text nor tool calls")
        return "\n".join(text_parts), tuple(calls)


__all__ = ["GeminiDirectorConfig", "GeminiDirectorError", "GeminiDirectorProvider"]
