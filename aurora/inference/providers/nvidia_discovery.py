"""NVIDIA NIM model-catalog discovery adapter."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..discovery import DiscoveredModelCandidate, candidate_id
from ..models import ModelSpec


class NVIDIAModelDiscoveryError(RuntimeError):
    """Raised when the NVIDIA model catalog cannot be read or parsed."""


class NVIDIAModelDiscoveryProvider:
    """Discover models exposed by an NVIDIA NIM OpenAI-compatible endpoint."""

    provider_id = "nvidia_api"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY", "")
        self.timeout_seconds = timeout_seconds
        if not self.base_url:
            raise NVIDIAModelDiscoveryError("NVIDIA model discovery base URL is empty")
        if self.timeout_seconds <= 0:
            raise NVIDIAModelDiscoveryError("NVIDIA model discovery timeout must be positive")

    def discover(self) -> tuple[DiscoveredModelCandidate, ...]:
        endpoint = f"{self.base_url}/models"
        headers = {"Accept": "application/json"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = Request(endpoint, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise NVIDIAModelDiscoveryError(
                f"NVIDIA model discovery HTTP {exc.code}: {detail}"
            ) from None
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise NVIDIAModelDiscoveryError(
                f"NVIDIA model discovery transport failure: {exc}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NVIDIAModelDiscoveryError(
                "NVIDIA model discovery returned invalid JSON"
            ) from None

        if not isinstance(payload, dict):
            raise NVIDIAModelDiscoveryError(
                "NVIDIA model discovery response must be a JSON object"
            )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise NVIDIAModelDiscoveryError(
                "NVIDIA model discovery response requires a data list"
            )

        discovered_at = datetime.now(timezone.utc).isoformat()
        candidates: list[DiscoveredModelCandidate] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                continue

            capabilities = _string_tuple(row.get("capabilities"))
            if not capabilities:
                capabilities = _infer_capabilities(model_id)
            specialties = _string_tuple(row.get("specialties"))
            context_window = _positive_int(row.get("context_window"))
            normalized_model_id = model_id.strip()
            model = ModelSpec(
                id=normalized_model_id,
                provider=self.provider_id,
                endpoint=self.base_url,
                capabilities=capabilities,
                context_window=context_window,
                enabled=False,
                specialties=specialties,
                quality_score=_catalog_quality_score(normalized_model_id),
                latency_score=_catalog_latency_score(normalized_model_id),
                cost_score=_catalog_latency_score(normalized_model_id),
                healthy=False,
                available=False,
            )
            metadata = {
                key: _safe_metadata_value(value)
                for key, value in row.items()
                if key not in {
                    "id",
                    "capabilities",
                    "specialties",
                    "context_window",
                }
            }
            candidates.append(
                DiscoveredModelCandidate(
                    candidate_id=candidate_id(self.provider_id, model.id),
                    provider_id=self.provider_id,
                    model=model,
                    metadata=metadata,
                    discovered_at=discovered_at,
                )
            )

        return tuple(sorted(candidates, key=lambda item: item.model.id))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        sorted(
            {
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            }
        )
    )


_NON_CONVERSATIONAL_MARKERS = (
    "embed",
    "guard",
    "safety",
    "detector",
    "parse",
    "reward",
    "calibration",
    "clip",
    "translate",
    "deplot",
    "diffusion",
)


def _infer_capabilities(model_id: str) -> tuple[str, ...]:
    """Conservatively classify catalog rows that omit capability metadata."""
    normalized = model_id.strip().lower()
    if any(marker in normalized for marker in _NON_CONVERSATIONAL_MARKERS):
        return ()
    return ("chat", "reasoning")


_QUALITY_HINT_MARKERS = (
    "reason",
    "think",
    "instruct",
    "chat",
    "gpt",
    "gemma",
    "llama",
    "mistral",
    "qwen",
    "kimi",
    "glm",
    "nemotron",
    "deepseek",
    "granite",
    "phi",
    "jamba",
    "zamba",
    "palmyra",
    "yi-",
)


def _catalog_quality_score(model_id: str) -> float:
    normalized = model_id.strip().lower()
    if any(marker in normalized for marker in _QUALITY_HINT_MARKERS):
        return 0.85
    return 0.65


def _catalog_latency_score(model_id: str) -> float:
    normalized = model_id.strip().lower()
    if any(marker in normalized for marker in ("flash", "nano", "mini", "small", "lightning")):
        return 0.8
    return 0.6


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _safe_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_safe_metadata_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _safe_metadata_value(item)
            for key, item in value.items()
        }
    return str(value)


__all__ = [
    "NVIDIAModelDiscoveryError",
    "NVIDIAModelDiscoveryProvider",
]
