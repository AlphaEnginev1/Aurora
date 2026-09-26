"""NVIDIA-hosted NIM API adapter using the OpenAI-compatible chat contract."""

import json
import os
import urllib.error
import urllib.request

from ..models import InferenceRequest, InferenceResponse, ModelSpec

class NVIDIAAPIError(RuntimeError):
    """Raised when an NVIDIA-hosted inference request cannot be completed."""

class NVIDIAAPIProvider:
    def __init__(self, spec: ModelSpec, api_key: str | None = None, timeout: float = 60.0):
        self.spec = spec
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY")
        self.timeout = timeout

    def health(self) -> bool:
        return bool(self.spec.enabled and self.spec.endpoint and self.api_key)

    def generate(self, request: InferenceRequest) -> InferenceResponse:
        if not self.health():
            raise NVIDIAAPIError("NVIDIA provider is not configured")
        payload = {"model": request.model or self.spec.id, "messages": request.messages}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        req = urllib.request.Request(
            self.spec.endpoint.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise NVIDIAAPIError(f"NVIDIA inference request failed: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NVIDIAAPIError("NVIDIA inference response was not valid JSON") from exc
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise NVIDIAAPIError("NVIDIA response did not contain choices[0].message.content") from exc
        return InferenceResponse(model=self.spec.id, provider=self.spec.provider, content=content, raw=raw)
