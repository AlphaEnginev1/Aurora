"""Local operator settings and credential storage for Aurora.

The file is intentionally kept inside the ignored .aurora-runtime directory.
API secrets never belong in the repository, dashboard HTML, or ordinary logs.
"""
from __future__ import annotations
import json, os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

DEFAULT_SETTINGS: dict[str, Any] = {
    "director": {"provider": "gemini", "model": "gemini-3.7-flash", "endpoint": "https://generativelanguage.googleapis.com/v1beta"},
    "providers": {
        "gemini": {"enabled": True, "api_key": ""},
        "nvidia_api": {"enabled": True, "api_key": "", "base_url": "https://integrate.api.nvidia.com/v1"},
    },
}

class OperatorSettingsError(RuntimeError):
    """Raised when local operator settings are invalid or cannot be saved."""

class OperatorSettingsStore:
    """Persist provider configuration outside source control with strict permissions."""
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        with self._lock:
            return _merge(DEFAULT_SETTINGS, self._read())

    def save(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise OperatorSettingsError("operator settings must be an object")
        with self._lock:
            merged = _merge(self.load(), payload)
            _validate(merged)
            self.path.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            _chmod_private(self.path)
            return merged

    def public(self) -> dict[str, Any]:
        data = self.load()
        providers: dict[str, Any] = {}
        for provider_id, provider in data["providers"].items():
            item = dict(provider)
            key = str(item.pop("api_key", "") or "")
            item["api_key_present"] = bool(key.strip())
            providers[provider_id] = item
        return {"director": dict(data["director"]), "providers": providers}

    def credential(self, provider_id: str) -> str:
        provider = self.load()["providers"].get(provider_id)
        return str(provider.get("api_key", "") or "").strip() if isinstance(provider, Mapping) else ""

    def update_from_form(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        current = self.load()
        next_data = _merge(current, payload)
        incoming_providers = payload.get("providers", {})
        if isinstance(incoming_providers, Mapping):
            for provider_id in ("gemini", "nvidia_api"):
                incoming = incoming_providers.get(provider_id, {})
                if isinstance(incoming, Mapping) and "api_key" in incoming:
                    value = str(incoming.get("api_key") or "")
                    if not value.strip():
                        next_data["providers"][provider_id]["api_key"] = current["providers"][provider_id].get("api_key", "")
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

def _validate(data: Mapping[str, Any]) -> None:
    director, providers = data.get("director"), data.get("providers")
    if not isinstance(director, Mapping) or not isinstance(providers, Mapping):
        raise OperatorSettingsError("operator settings require director and providers objects")
    provider = str(director.get("provider", "")).strip().lower()
    if provider not in {"gemini", "router", "nvidia_api"}:
        raise OperatorSettingsError("director.provider must be gemini, router, or nvidia_api")
    if not str(director.get("model", "")).strip():
        raise OperatorSettingsError("director.model cannot be empty")
    if not str(director.get("endpoint", "")).strip().startswith("https://"):
        raise OperatorSettingsError("director.endpoint must use HTTPS")
    for provider_id in ("gemini", "nvidia_api"):
        provider_data = providers.get(provider_id)
        if not isinstance(provider_data, Mapping):
            raise OperatorSettingsError(f"provider configuration missing: {provider_id}")
        if provider_id == "nvidia_api" and not str(provider_data.get("base_url", "")).strip().startswith("https://"):
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

__all__ = ["DEFAULT_SETTINGS","OperatorSettingsError","OperatorSettingsStore","settings_path"]