"""Central free-model policy used by Aurora's runtime routing and discovery.

The policy is deliberately fail-closed. Static providers require an explicit free record.
NVIDIA may additionally authorize models discovered from its live Free Endpoint catalog;
the checked-in NVIDIA entries are seed metadata only and never form an allowlist ceiling.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

DEFAULT_FREE_MODEL_POLICY = Path(__file__).resolve().parents[2] / "config/free-model-policy.json"


class FreeModelPolicyError(ValueError):
    """Raised when Aurora's free-model policy is missing or invalid."""


def policy_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.getenv("AURORA_FREE_MODEL_POLICY", "").strip()
    return Path(configured) if configured else DEFAULT_FREE_MODEL_POLICY


def load_free_model_policy(path: str | Path | None = None) -> dict[str, Any]:
    source = policy_path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreeModelPolicyError(f"unable to load free-model policy: {source}") from exc
    if not isinstance(data, dict) or data.get("mode") != "free_only":
        raise FreeModelPolicyError("free-model policy must declare mode=free_only")
    providers = data.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise FreeModelPolicyError("free-model policy must contain providers")
    return data


def model_record(provider_id: str, model_id: str, path: str | Path | None = None) -> Mapping[str, Any] | None:
    data = load_free_model_policy(path)
    providers = data.get("providers", {})
    provider = providers.get(str(provider_id).strip().lower())
    if not isinstance(provider, Mapping):
        return None
    models = provider.get("models")
    if not isinstance(models, Mapping):
        return None
    record = models.get(str(model_id).strip())
    return record if isinstance(record, Mapping) else None


def dynamic_catalog_mode(provider_id: str, path: str | Path | None = None) -> str | None:
    data = load_free_model_policy(path)
    provider = data["providers"].get(str(provider_id).strip().lower(), {})
    if not isinstance(provider, Mapping):
        return None
    mode = provider.get("dynamic_catalog")
    return str(mode).strip().lower() if isinstance(mode, str) and mode.strip() else None


def is_discovered_free_model(provider_id: str, model_id: str, *, discovered_free_endpoint: bool = False, path: str | Path | None = None) -> bool:
    """Return whether a discovered model is currently authorized as free.

    NVIDIA's dynamic free-endpoint mode is deliberately fail-closed: checked-in
    seed records provide metadata only and cannot authorize a served model that
    is absent from the current official Free Endpoint catalog.
    """
    normalized_provider = str(provider_id).strip().lower()
    if normalized_provider == "nvidia_api" and dynamic_catalog_mode(provider_id, path) == "free_endpoint":
        return bool(discovered_free_endpoint)
    return is_free_model(provider_id, model_id, path)


def is_free_model(provider_id: str, model_id: str, path: str | Path | None = None) -> bool:
    record = model_record(provider_id, model_id, path)
    return bool(record and str(record.get("tier", "")).strip().lower() == "free")


def free_model_ids(provider_id: str, path: str | Path | None = None) -> tuple[str, ...]:
    data = load_free_model_policy(path)
    provider = data["providers"].get(str(provider_id).strip().lower(), {})
    models = provider.get("models", {}) if isinstance(provider, Mapping) else {}
    if not isinstance(models, Mapping):
        return ()
    ranked: list[tuple[int, str]] = []
    for model_id, record in models.items():
        if not isinstance(record, Mapping):
            continue
        if str(record.get("tier", "")).strip().lower() != "free":
            continue
        try:
            rank = int(record.get("rank", 9999))
        except (TypeError, ValueError):
            rank = 9999
        ranked.append((rank, str(model_id)))
    return tuple(model_id for _rank, model_id in sorted(ranked))


def record_metadata(provider_id: str, model_id: str, path: str | Path | None = None) -> dict[str, Any]:
    record = model_record(provider_id, model_id, path)
    return dict(record) if record is not None else {}


__all__ = [
    "DEFAULT_FREE_MODEL_POLICY",
    "FreeModelPolicyError",
    "dynamic_catalog_mode",
    "free_model_ids",
    "is_discovered_free_model",
    "is_free_model",
    "load_free_model_policy",
    "model_record",
    "policy_path",
    "record_metadata",
]
