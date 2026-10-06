"""Central free-model policy used by Aurora's runtime routing and discovery.

The policy is deliberately fail-closed: a model is considered free only when it is
explicitly present in the curated policy with tier="free". Provider discovery can
stage unknown models, but unknown/paid models can never enter a free-only route.
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
    "free_model_ids",
    "is_free_model",
    "load_free_model_policy",
    "model_record",
    "policy_path",
    "record_metadata",
]
