"""Local operator settings and securely redacted provider credential storage for Aurora."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from urllib.parse import urlparse


_FALLBACK_NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1"

# Explicit fallback allow-list. Only these providers have an approved free-only
# execution route in config/free-model-policy.json. Configuring other API keys
# does not grant permission to invoke those providers for Director failover.
ALLOWED_DIRECTOR_FAILOVER_PROVIDERS = ("nvidia_api", "openrouter")


def _load_default_provider_endpoints() -> dict[str, str]:
    """Read endpoint defaults from the canonical provider catalog.

    A minimal NVIDIA fallback keeps old installations importable only when the
    catalog file is absent. If the file exists but is malformed, fail closed
    instead of silently reintroducing a second hard-coded provider catalog.
    """
    catalog_path = Path(__file__).resolve().parents[1] / "config" / "providers.json"
    if not catalog_path.exists():
        return {"nvidia_api": _FALLBACK_NVIDIA_ENDPOINT}
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("canonical provider catalog is unreadable") from exc
    providers = raw.get("providers") if isinstance(raw, Mapping) else None
    if not isinstance(providers, Mapping):
        raise RuntimeError("canonical provider catalog has no providers mapping")
    endpoints: dict[str, str] = {}
    for raw_id, metadata in providers.items():
        provider_id = str(raw_id).strip().lower()
        if not provider_id or not isinstance(metadata, Mapping):
            raise RuntimeError("canonical provider catalog contains an invalid provider entry")
        endpoint = metadata.get("base_url", "")
        if not isinstance(endpoint, str):
            raise RuntimeError(f"canonical provider endpoint must be a string: {provider_id}")
        endpoints[provider_id] = endpoint.strip().rstrip("/")
    endpoints.setdefault("nvidia_api", _FALLBACK_NVIDIA_ENDPOINT)
    return endpoints


DEFAULT_PROVIDER_ENDPOINTS = _load_default_provider_endpoints()
NVIDIA_ENDPOINT = DEFAULT_PROVIDER_ENDPOINTS["nvidia_api"] or _FALLBACK_NVIDIA_ENDPOINT
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
    "github": {
        "access_token": "",
        "login": "",
        "name": "",
        "avatar_url": "",
        "scopes": [],
        "connected_at": "",
    },
}


class OperatorSettingsError(RuntimeError):
    """Raised when local operator settings are invalid or cannot be saved."""


class OperatorSettingsStore:
    """Persist local provider credentials and free-only Director routing outside source control."""

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
            merged_payload = _merge(self.load(), payload)
            _validate_failover_order(merged_payload)
            _validate_provider_enabled_flags(merged_payload)
            merged = _normalize(merged_payload)
            _validate(merged)
            self.path.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _chmod_private(self.path)
            return merged

    def public(self) -> dict[str, Any]:
        data = self.load()
        providers: dict[str, Any] = {}
        for provider_id, config in data.get("providers", {}).items():
            if not isinstance(config, Mapping):
                continue
            public_config = dict(config)
            key = str(public_config.pop("api_key", "") or "")
            public_config["api_key_present"] = bool(key.strip())
            providers[provider_id] = public_config
        github = dict(data.get("github", {}))
        github_token = str(github.pop("access_token", "") or "")
        github["access_token_present"] = bool(github_token.strip())
        return {
            "director": dict(data["director"]),
            "providers": providers,
            "github": github,
        }

    def credential(self, provider_id: str) -> str:
        provider_id = str(provider_id).strip().lower()
        if provider_id == "github":
            github = self.load().get("github", {})
            return str(github.get("access_token", "") or "").strip()
        provider = self.load().get("providers", {}).get(provider_id, {})
        if not isinstance(provider, Mapping):
            return ""
        return str(provider.get("api_key", "") or "").strip()

    def set_github_connection(
        self,
        *,
        access_token: str,
        login: str,
        name: str = "",
        avatar_url: str = "",
        scopes: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if not access_token.strip() or not login.strip():
            raise OperatorSettingsError("GitHub connection requires access_token and login")
        from datetime import datetime, timezone
        return self.save({
            "github": {
                "access_token": access_token.strip(),
                "login": login.strip(),
                "name": name.strip(),
                "avatar_url": avatar_url.strip(),
                "scopes": [str(item).strip() for item in scopes if str(item).strip()],
                "connected_at": datetime.now(timezone.utc).isoformat(),
            }
        })

    def clear_github_connection(self) -> dict[str, Any]:
        return self.save({
            "github": {
                "access_token": "",
                "login": "",
                "name": "",
                "avatar_url": "",
                "scopes": [],
                "connected_at": "",
            }
        })

    def update_from_form(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        current = self.load()
        next_data = _merge(current, payload)
        incoming = payload.get("providers", {}) if isinstance(payload, Mapping) else {}
        if isinstance(incoming, Mapping):
            for provider_id, config in incoming.items():
                if not isinstance(config, Mapping) or "api_key" not in config:
                    continue
                value = str(config.get("api_key") or "")
                if not value.strip():
                    existing = current.get("providers", {}).get(provider_id, {})
                    next_data.setdefault("providers", {}).setdefault(provider_id, {})["api_key"] = (
                        existing.get("api_key", "") if isinstance(existing, Mapping) else ""
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
    if provider not in {"router", "nvidia_api", "openrouter"}:
        # Providers without an explicitly approved free-only runtime route do
        # not become Director primaries merely because their API key is stored.
        provider = "router"
    director["provider"] = provider
    director["endpoint"] = NVIDIA_ENDPOINT
    # Only explicit Settings choices and a successfully activated failover
    # route override launcher defaults. Legacy provider values remain inert.
    provider_source = str(director.get("provider_source", "")).strip().lower()
    director["provider_source"] = (
        provider_source
        if provider_source in {"operator_settings", "runtime_failover"}
        else ""
    )
    model = str(director.get("model", "")).strip()
    if provider == "router":
        # Router mode owns model selection dynamically. Never persist or resurrect
        # a historical provider model in this mode.
        director["model"] = "router"
    elif provider == "openrouter":
        # Only the canonical official free router is permitted for direct mode.
        director["model"] = "openrouter/free"
    elif not _is_nvidia_model_id(model):
        # Direct NVIDIA mode requires an explicit NVIDIA model. The selected model
        # is validated against the live discovery pool before execution.
        director["model"] = DEFAULT_NVIDIA_MODEL
    failover = director.get("failover", {})
    if not isinstance(failover, Mapping):
        failover = {}
    raw_order = failover.get("order", ["nvidia_api"])
    # Migrate old/unknown entries away rather than ever enabling a provider whose
    # free-only policy is not explicitly approved. The default remains fail-closed.
    normalized_order: list[str] = []
    if isinstance(raw_order, list):
        for item in raw_order:
            if not isinstance(item, str):
                continue
            provider_id = item.strip().lower()
            if (
                provider_id in ALLOWED_DIRECTOR_FAILOVER_PROVIDERS
                and provider_id not in normalized_order
            ):
                normalized_order.append(provider_id)
    if not normalized_order:
        normalized_order = ["nvidia_api"]
    failover_enabled = failover.get("enabled", True)
    # A malformed persisted flag must never turn recovery back on silently.
    if not isinstance(failover_enabled, bool):
        failover_enabled = False
    director["failover"] = {
        "enabled": failover_enabled,
        "order": normalized_order,
    }
    provider_configs = result.get("providers", {})
    if not isinstance(provider_configs, Mapping):
        provider_configs = {}
    normalized_providers: dict[str, Any] = {}
    for raw_id, raw_config in provider_configs.items():
        provider_id = str(raw_id).strip().lower()
        if not provider_id or not all(ch.isalnum() or ch in "_-" for ch in provider_id):
            continue
        if not isinstance(raw_config, Mapping):
            continue
        default_url = DEFAULT_PROVIDER_ENDPOINTS.get(provider_id, "")
        base_url = str(raw_config.get("base_url", default_url) or default_url).strip().rstrip("/")
        provider_enabled = raw_config.get("enabled", True)
        if not isinstance(provider_enabled, bool):
            provider_enabled = False
        normalized_providers[provider_id] = {
            "enabled": provider_enabled,
            "api_key": str(raw_config.get("api_key", "") or ""),
            "base_url": base_url,
        }
    if "nvidia_api" not in normalized_providers:
        normalized_providers["nvidia_api"] = {
            "enabled": True,
            "api_key": "",
            "base_url": NVIDIA_ENDPOINT,
        }
    result["providers"] = normalized_providers
    github = result.get("github", {})
    if not isinstance(github, Mapping):
        github = {}
    scopes = github.get("scopes", [])
    result["github"] = {
        "access_token": str(github.get("access_token", "") or ""),
        "login": str(github.get("login", "") or ""),
        "name": str(github.get("name", "") or ""),
        "avatar_url": str(github.get("avatar_url", "") or ""),
        "scopes": [str(item).strip() for item in scopes if str(item).strip()] if isinstance(scopes, list) else [],
        "connected_at": str(github.get("connected_at", "") or ""),
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
    if provider not in {"router", "nvidia_api", "openrouter"}:
        raise OperatorSettingsError("director.provider must be router, nvidia_api, or openrouter")
    model = str(director.get("model", "")).strip()
    if not model:
        raise OperatorSettingsError("director.model cannot be empty")
    if provider == "router" and model != "router":
        raise OperatorSettingsError("router director must not pin a provider model")
    if provider == "nvidia_api":
        if not _is_nvidia_model_id(model):
            raise OperatorSettingsError("nvidia_api director.model must be an NVIDIA model identifier")
        nvidia = providers.get("nvidia_api", {})
        if isinstance(nvidia, Mapping) and nvidia.get("enabled", True) is False:
            raise OperatorSettingsError("NVIDIA Director mode cannot use a disabled provider")
        stored_key = str(nvidia.get("api_key", "") or "") if isinstance(nvidia, Mapping) else ""
        if not stored_key.strip() and not os.getenv("NVIDIA_API_KEY", "").strip():
            raise OperatorSettingsError("NVIDIA Director mode requires an NVIDIA API key")
    if provider == "openrouter":
        if model != "openrouter/free":
            raise OperatorSettingsError("OpenRouter Director mode is restricted to openrouter/free")
        openrouter = providers.get("openrouter", {})
        if isinstance(openrouter, Mapping) and openrouter.get("enabled", True) is False:
            raise OperatorSettingsError("OpenRouter Director mode cannot use a disabled provider")
        stored_key = str(openrouter.get("api_key", "") or "") if isinstance(openrouter, Mapping) else ""
        if not stored_key.strip() and not os.getenv("OPENROUTER_API_KEY", "").strip():
            raise OperatorSettingsError("OpenRouter Director mode requires an OpenRouter API key")
    if not str(director.get("endpoint", "")).strip().startswith("https://"):
        raise OperatorSettingsError("director.endpoint must use HTTPS")
    failover = director.get("failover", {})
    if not isinstance(failover, Mapping):
        raise OperatorSettingsError("director.failover must be an object")
    if failover.get("enabled", True) is not True and failover.get("enabled", True) is not False:
        raise OperatorSettingsError("director.failover.enabled must be boolean")
    order = failover.get("order", [])
    if (
        not isinstance(order, list)
        or not order
        or any(
            not isinstance(item, str)
            or item != item.strip().lower()
            or item not in ALLOWED_DIRECTOR_FAILOVER_PROVIDERS
            for item in order
        )
        or len(order) != len(set(order))
    ):
        raise OperatorSettingsError(
            "director.failover.order must be a non-empty, unique ordered list "
            "containing only approved free-only providers: "
            + ", ".join(ALLOWED_DIRECTOR_FAILOVER_PROVIDERS)
        )
    nvidia = providers.get("nvidia_api")
    if not isinstance(nvidia, Mapping):
        raise OperatorSettingsError("provider configuration missing: nvidia_api")
    for provider_id, config in providers.items():
        if not isinstance(config, Mapping):
            raise OperatorSettingsError(f"{provider_id}.configuration must be an object")
        endpoint = str(config.get("base_url", "") or "").strip()
        if not endpoint:
            continue
        try:
            parsed = urlparse(endpoint)
            hostname = (parsed.hostname or "").lower()
            # Accessing .port also validates that a supplied port is numeric
            # and within the valid TCP range.
            _ = parsed.port
        except ValueError:
            raise OperatorSettingsError(f"{provider_id}.base_url is not a valid URL") from None
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise OperatorSettingsError(
                f"{provider_id}.base_url must not contain embedded credentials, query parameters, or fragments"
            )
        local_http = parsed.scheme == "http" and hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not local_http:
            raise OperatorSettingsError(
                f"{provider_id}.base_url must use HTTPS (HTTP is allowed only for loopback services)"
            )


def _validate_provider_enabled_flags(data: Mapping[str, Any]) -> None:
    """Reject non-boolean provider enablement before settings are normalized."""
    providers = data.get("providers", {})
    if not isinstance(providers, Mapping):
        raise OperatorSettingsError("operator settings providers must be an object")
    for provider_id, config in providers.items():
        if not isinstance(config, Mapping):
            raise OperatorSettingsError(f"{provider_id}.configuration must be an object")
        if "enabled" in config and not isinstance(config["enabled"], bool):
            raise OperatorSettingsError(f"{provider_id}.enabled must be boolean")


def _validate_failover_order(data: Mapping[str, Any]) -> None:
    """Reject unsafe order updates before normalization can discard them."""
    director = data.get("director", {})
    failover = director.get("failover", {}) if isinstance(director, Mapping) else {}
    if not isinstance(failover, Mapping):
        return
    if "enabled" in failover and not isinstance(failover["enabled"], bool):
        raise OperatorSettingsError("director.failover.enabled must be boolean")
    if "order" not in failover:
        return
    order = failover["order"]
    if (
        not isinstance(order, list)
        or not order
        or any(
            not isinstance(item, str)
            or item.strip() != item
            or item.lower() != item
            or item not in ALLOWED_DIRECTOR_FAILOVER_PROVIDERS
            for item in order
        )
        or len(order) != len(set(order))
    ):
        raise OperatorSettingsError(
            "director.failover.order may contain only a non-empty, duplicate-free "
            "ordered list of approved free-only providers: "
            + ", ".join(ALLOWED_DIRECTOR_FAILOVER_PROVIDERS)
        )


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
