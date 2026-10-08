"""Local NVIDIA-only operator settings and credential storage for Aurora."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping


NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"

DEFAULT_SETTINGS: dict[str, Any] = {
    "director": {
        "provider": "router",
        "model": "router",
        "endpoint": NVIDIA_ENDPOINT,
        "failover": {"enabled": True, "order": ["nvidia_api"]},
    },
    "providers": {
        "nvidia_api": {
            "enabled": True,
            "api_key": "",
            "base_url": NVIDIA_ENDPOINT,
        }
    },
}


class OperatorSettingsError(RuntimeError):
    """Raised when local operator settings are invalid or cannot be saved."""


class OperatorSettingsStore:
    """Persist only NVIDIA runtime configuration outside source control."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        with self._lock:
            raw = self._read()
            normalized = _normalize(_merge(DEFAULT_SETTINGS, raw))
            if self.path.exists() and raw != normalized:
                self.path.write_text(
                    json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                _chmod_private(self.path)
            return normalized

    def save(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise OperatorSettingsError("operator settings must be an object")
        with self._lock:
            merged = _normalize(_merge(self.load(), payload))
            _validate(merged)
            self.path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _chmod_private(self.path)
            return merged

    def public(self) -> dict[str, Any]:
        data = self.load()
        provider = dict(data["providers"]["nvidia_api"])
        key = str(provider.pop("api_key", "") or "")
        provider["api_key_present"] = bool(key.strip())
        return {
            "director": dict(data["director"]),
            "providers": {"nvidia_api": provider},
        }

    def credential(self, provider_id: str) -> str:
        if str(provider_id).strip().lower() != "nvidia_api":
            return ""
        provider = self.load()["providers"].get("nvidia_api", {})
        return str(provider.get("api_key", "") or "").strip()

    def update_from_form(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        current = self.load()
        next_data = _merge(current, payload)
        incoming = payload.get("providers", {}) if isinstance(payload, Mapping) else {}
        if isinstance(incoming, Mapping):
            nvidia = incoming.get("nvidia_api", {})
            if isinstance(nvidia, Mapping) and "api_key" in nvidia:
                value = str(nvidia.get("api_key") or "")
                if not value.strip():
                    next_data["providers"]["nvidia_api"]["api_key"] = (
                        current["providers"]["nvidia_api"].get("api_key", "")
                    )
        return self.save(next_data)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperatorSettingsError(f"unable to read operator settings: {self.path}") from exc
        if not isinstance(raw, dict):
            raise OperatorSettingsError("operator settings file must contain an object")
        return raw


def settings_path(workspace_root: str | Path) -> Path:
    return Path(workspace_root).resolve() / "operator-settings.json"


def _normalize(data: Mapping[str, Any]) -> dict[str, Any]:
    result = _merge(DEFAULT_SETTINGS, data)
    director = result["director"]
    provider = str(director.get("provider", "router")).strip().lower()
    if provider not in {"router", "nvidia_api"}:
        # Legacy external providers are migrated to the canonical Router path.
        # Runtime startup does not persist this normalization; the explicit
        # operator migration helper owns durable migration when requested.
        provider = "router"
    director["provider"] = provider
    director["endpoint"] = NVIDIA_ENDPOINT
    model = str(director.get("model", "")).strip()
    if provider == "router":
        # Router mode owns model selection dynamically. Never persist or resurrect
        # a historical NVIDIA model as the Director model in this mode.
        director["model"] = "router"
    elif not _is_nvidia_model_id(model):
        # Direct NVIDIA mode requires an explicit NVIDIA model. The selected model
        # is validated against the live discovery pool before execution.
        director["model"] = DEFAULT_NVIDIA_MODEL
    failover = director.get("failover", {})
    if not isinstance(failover, Mapping):
        failover = {}
    director["failover"] = {
        "enabled": bool(failover.get("enabled", True)),
        "order": ["nvidia_api"],
    }
    nvidia = result.get("providers", {}).get("nvidia_api", {})
    if not isinstance(nvidia, Mapping):
        nvidia = {}
    result["providers"] = {
        "nvidia_api": {
            "enabled": bool(nvidia.get("enabled", True)),
            "api_key": str(nvidia.get("api_key", "") or ""),
            "base_url": str(nvidia.get("base_url", NVIDIA_ENDPOINT) or NVIDIA_ENDPOINT).rstrip("/"),
        }
    }
    return result


def _is_nvidia_model_id(model: str) -> bool:
    """Accept NVIDIA model identifiers, including dynamically discovered models."""
    normalized = str(model).strip()
    return "/" in normalized and not normalized.startswith(("gemini", "gpt-", "claude"))


def _validate(data: Mapping[str, Any]) -> None:
    director = data.get("director")
    providers = data.get("providers")
    if not isinstance(director, Mapping) or not isinstance(providers, Mapping):
        raise OperatorSettingsError("operator settings require director and providers objects")
    provider = str(director.get("provider", "")).strip().lower()
    if provider not in {"router", "nvidia_api"}:
        raise OperatorSettingsError("director.provider must be router or nvidia_api")
    model = str(director.get("model", "")).strip()
    if not model:
        raise OperatorSettingsError("director.model cannot be empty")
    if provider == "router" and model != "router":
        raise OperatorSettingsError("router director must not pin a provider model")
    if provider == "nvidia_api" and not _is_nvidia_model_id(model):
        raise OperatorSettingsError("nvidia_api director.model must be an NVIDIA model identifier")
    if not str(director.get("endpoint", "")).strip().startswith("https://"):
        raise OperatorSettingsError("director.endpoint must use HTTPS")
    failover = director.get("failover", {})
    if not isinstance(failover, Mapping):
        raise OperatorSettingsError("director.failover must be an object")
    if failover.get("enabled", True) is not True and failover.get("enabled", True) is not False:
        raise OperatorSettingsError("director.failover.enabled must be boolean")
    order = failover.get("order", [])
    if order != ["nvidia_api"]:
        raise OperatorSettingsError("director.failover.order must be exactly ['nvidia_api']")
    nvidia = providers.get("nvidia_api")
    if not isinstance(nvidia, Mapping):
        raise OperatorSettingsError("provider configuration missing: nvidia_api")
    if not str(nvidia.get("base_url", "")).strip().startswith("https://"):
        raise OperatorSettingsError("nvidia_api.base_url must use HTTPS")


def _merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = {str(k): _deepcopy(v) for k, v in base.items()}
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge(result[key], value)
        else:
            result[key] = _deepcopy(value)
    return result


def _deepcopy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _deepcopy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deepcopy(v) for v in value]
    return value


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise OperatorSettingsError(f"unable to restrict permissions on {path}") from exc


__all__ = [
    "DEFAULT_NVIDIA_MODEL",
    "DEFAULT_SETTINGS",
    "NVIDIA_ENDPOINT",
    "OperatorSettingsError",
    "OperatorSettingsStore",
    "settings_path",
]
