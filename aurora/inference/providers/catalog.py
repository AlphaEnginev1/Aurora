"""Validated reader for the declarative provider metadata catalog.

The catalog contains public metadata only. Adapter implementations are selected
from an allow-list in registry.py; JSON never controls Python imports or code.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


class ProviderCatalogError(ValueError):
    """Raised when the provider catalog is missing or invalid."""


VALID_REGISTRY_MODES = {
    "default",
    "include_external",
    "include_openrouter",
    "catalog_only",
}
LOCAL_HTTP_PROVIDERS = {"ollama", "lm_studio"}
REQUIRED_FIELDS = {
    "name",
    "base_url",
    "protocol",
    "adapter",
    "adapter_available",
    "registry_mode",
    "runtime_enabled_by_default",
    "credential_env",
    "auth_required",
    "capabilities",
    "discovery",
}


def default_catalog_path() -> Path:
    """Resolve the repository catalog independently of the process CWD."""
    configured = os.getenv("AURORA_PROVIDER_CATALOG", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    repository_path = Path(__file__).resolve().parents[3] / "config" / "providers.json"
    if repository_path.is_file():
        return repository_path
    installed_path = Path(sys.prefix) / "config" / "providers.json"
    if installed_path.is_file():
        return installed_path
    # Return the primary expected location so the error identifies it clearly.
    return repository_path


class ProviderCatalog:
    """Read-only, validated provider metadata loaded from providers.json."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser().resolve() if path is not None else default_catalog_path()
        self._providers = self._read_and_validate()

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    @property
    def providers(self) -> tuple[dict[str, Any], ...]:
        """Return metadata copies in stable ID order; never include credentials."""
        return tuple(
            {"provider_id": provider_id, **dict(entry)}
            for provider_id, entry in self._providers.items()
        )

    def resolve(self, provider_id: str) -> dict[str, Any]:
        normalized = str(provider_id).strip().lower()
        if not normalized:
            raise ProviderCatalogError("provider ID is empty")
        try:
            return dict(self._providers[normalized])
        except KeyError as exc:
            raise ProviderCatalogError(f"provider is not in catalog: {normalized}") from exc

    def _read_and_validate(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ProviderCatalogError(f"provider catalog is unavailable: {self.path}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderCatalogError("provider catalog is not valid UTF-8 JSON") from exc

        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ProviderCatalogError("provider catalog schema_version must be 1")
        entries = raw.get("providers")
        if not isinstance(entries, dict) or not entries:
            raise ProviderCatalogError("provider catalog must contain a non-empty providers object")

        normalized: dict[str, dict[str, Any]] = {}
        for raw_id, raw_entry in entries.items():
            provider_id = str(raw_id).strip().lower()
            if not provider_id or not re.fullmatch(r"[a-z0-9_]+", provider_id):
                raise ProviderCatalogError("provider catalog contains an invalid provider ID")
            if provider_id in normalized:
                raise ProviderCatalogError(f"duplicate provider ID: {provider_id}")
            if not isinstance(raw_entry, dict):
                raise ProviderCatalogError(f"provider entry is not an object: {provider_id}")
            missing = REQUIRED_FIELDS - set(raw_entry)
            if missing:
                raise ProviderCatalogError(
                    f"provider entry {provider_id} is missing required fields: {', '.join(sorted(missing))}"
                )

            name = raw_entry.get("name")
            endpoint = raw_entry.get("base_url")
            protocol = raw_entry.get("protocol")
            adapter = raw_entry.get("adapter")
            adapter_available = raw_entry.get("adapter_available")
            registry_mode = raw_entry.get("registry_mode")
            default_enabled = raw_entry.get("runtime_enabled_by_default")
            credential_env = raw_entry.get("credential_env")
            auth_required = raw_entry.get("auth_required")
            capabilities = raw_entry.get("capabilities")
            discovery = raw_entry.get("discovery")

            if not isinstance(name, str) or not name.strip():
                raise ProviderCatalogError(f"provider name is empty: {provider_id}")
            if not isinstance(endpoint, str):
                raise ProviderCatalogError(f"provider endpoint must be a string: {provider_id}")
            if not isinstance(protocol, str) or not protocol.strip():
                raise ProviderCatalogError(f"provider protocol is empty: {provider_id}")
            if adapter is not None and (not isinstance(adapter, str) or not adapter.strip()):
                raise ProviderCatalogError(f"provider adapter is invalid: {provider_id}")
            if not isinstance(adapter_available, bool):
                raise ProviderCatalogError(f"adapter_available must be boolean: {provider_id}")
            if adapter_available != (adapter is not None):
                raise ProviderCatalogError(f"adapter metadata is inconsistent: {provider_id}")
            if not isinstance(registry_mode, str) or registry_mode not in VALID_REGISTRY_MODES:
                raise ProviderCatalogError(f"invalid registry_mode for provider: {provider_id}")
            if not isinstance(default_enabled, bool):
                raise ProviderCatalogError(f"runtime_enabled_by_default must be boolean: {provider_id}")
            if not isinstance(auth_required, bool):
                raise ProviderCatalogError(f"auth_required must be boolean: {provider_id}")
            if credential_env is not None and (
                not isinstance(credential_env, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]*", credential_env)
            ):
                raise ProviderCatalogError(f"credential_env is invalid: {provider_id}")
            if not isinstance(capabilities, list) or not capabilities or not all(
                isinstance(value, str) and value.strip() for value in capabilities
            ):
                raise ProviderCatalogError(f"capabilities must be a non-empty string list: {provider_id}")
            if not isinstance(discovery, dict) or not isinstance(discovery.get("enabled"), bool):
                raise ProviderCatalogError(f"discovery metadata is invalid: {provider_id}")
            discovery_adapter = discovery.get("adapter")
            if discovery_adapter is not None and (
                not isinstance(discovery_adapter, str) or not discovery_adapter.strip()
            ):
                raise ProviderCatalogError(f"discovery adapter is invalid: {provider_id}")
            if discovery["enabled"] != (discovery_adapter is not None):
                raise ProviderCatalogError(f"discovery metadata is inconsistent: {provider_id}")
            if registry_mode == "catalog_only" and (adapter_available or default_enabled):
                raise ProviderCatalogError(f"catalog-only provider cannot have an active adapter: {provider_id}")
            if registry_mode == "default" and not default_enabled:
                raise ProviderCatalogError(f"default provider must be enabled by default: {provider_id}")
            if registry_mode != "default" and default_enabled:
                raise ProviderCatalogError(f"non-default provider cannot be enabled by default: {provider_id}")
            if endpoint:
                parsed = urlparse(endpoint)
                if provider_id in LOCAL_HTTP_PROVIDERS:
                    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
                        raise ProviderCatalogError(f"local provider endpoint must use localhost HTTP: {provider_id}")
                elif parsed.scheme != "https" or not parsed.hostname:
                    raise ProviderCatalogError(f"provider endpoint must use HTTPS: {provider_id}")
            if provider_id == "custom_openai_compatible" and endpoint:
                # Custom endpoints are intentionally operator supplied.
                raise ProviderCatalogError("custom provider catalog endpoint must be empty")
            if "api_key" in raw_entry or "secret" in raw_entry:
                raise ProviderCatalogError(f"provider catalog must not contain secret values: {provider_id}")

            normalized[provider_id] = {
                **raw_entry,
                "name": name.strip(),
                "base_url": endpoint.rstrip("/") if endpoint and provider_id not in LOCAL_HTTP_PROVIDERS else endpoint,
                "protocol": protocol.strip(),
                "capabilities": tuple(sorted(set(value.strip() for value in capabilities))),
                "discovery": dict(discovery),
            }

        nvidia = normalized.get("nvidia_api")
        if nvidia is None:
            raise ProviderCatalogError("provider catalog is missing canonical nvidia_api entry")
        if (
            nvidia["base_url"] != "https://integrate.api.nvidia.com/v1"
            or nvidia["adapter"] != "nvidia_api"
            or nvidia["credential_env"] != "NVIDIA_API_KEY"
            or nvidia["registry_mode"] != "default"
        ):
            raise ProviderCatalogError("canonical NVIDIA provider metadata has changed unexpectedly")
        return normalized


__all__ = ["ProviderCatalog", "ProviderCatalogError", "default_catalog_path"]
