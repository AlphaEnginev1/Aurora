"""Contract tests for the declarative provider metadata catalog.

The catalog describes provider metadata and current adapter maturity only.
It must never contain API key values or claim that catalog-only entries can
execute inference.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config" / "providers.json"
UI_PATH = ROOT / "aurora" / "dashboard" / "ui" / "index.html"
ADAPTER_MODULES = {
    "nvidia_api": ROOT / "aurora" / "inference" / "providers" / "nvidia_api.py",
    "openai_api": ROOT / "aurora" / "inference" / "providers" / "openai_api.py",
    "anthropic_api": ROOT / "aurora" / "inference" / "providers" / "anthropic_api.py",
}
VALID_REGISTRY_MODES = {"default", "credential_gated", "catalog_only"}
LOCAL_HTTP_PROVIDERS = {"ollama", "lm_studio"}


def load_catalog() -> dict:
    assert CATALOG_PATH.is_file(), f"missing provider catalog: {CATALOG_PATH}"
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_provider_catalog_has_supported_schema_version():
    catalog = load_catalog()
    assert catalog.get("schema_version") == 1
    assert isinstance(catalog.get("providers"), dict)
    assert catalog["providers"]


def test_provider_entries_follow_the_declared_contract():
    providers = load_catalog()["providers"]
    required = {
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
    for provider_id, provider in providers.items():
        assert provider_id and re.fullmatch(r"[a-z0-9_]+", provider_id)
        assert isinstance(provider, dict), provider_id
        assert required <= set(provider), f"{provider_id} is missing catalog fields"
        assert isinstance(provider["name"], str) and provider["name"].strip()
        assert isinstance(provider["protocol"], str) and provider["protocol"].strip()
        assert provider["registry_mode"] in VALID_REGISTRY_MODES
        assert isinstance(provider["runtime_enabled_by_default"], bool)
        assert isinstance(provider["adapter_available"], bool)
        assert isinstance(provider["auth_required"], bool)
        assert isinstance(provider["capabilities"], list) and provider["capabilities"]
        assert all(isinstance(item, str) and item.strip() for item in provider["capabilities"])
        assert isinstance(provider["discovery"], dict)
        assert isinstance(provider["discovery"].get("enabled"), bool)
        assert provider["discovery"].get("adapter") is None or isinstance(
            provider["discovery"].get("adapter"), str
        )
        assert provider["adapter"] is None or isinstance(provider["adapter"], str)
        assert provider["credential_env"] is None or re.fullmatch(
            r"[A-Z][A-Z0-9_]*", provider["credential_env"]
        )
        endpoint = provider["base_url"]
        assert isinstance(endpoint, str)
        if endpoint:
            parsed = urlparse(endpoint)
            if provider_id in LOCAL_HTTP_PROVIDERS:
                assert parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
            else:
                assert parsed.scheme == "https" and parsed.hostname, provider_id


def test_catalog_contains_all_settings_selector_providers_without_drift():
    catalog_ids = set(load_catalog()["providers"])
    html = UI_PATH.read_text(encoding="utf-8")
    selector_match = re.search(
        r'<select id="credential-provider"[^>]*>(.*?)</select>',
        html,
        flags=re.DOTALL,
    )
    assert selector_match, "provider selector is missing from Settings"
    ui_ids = set(re.findall(r'<option value="([^"]+)"', selector_match.group(1)))
    assert ui_ids == catalog_ids


def test_nvidia_catalog_entry_preserves_canonical_runtime_contract():
    provider = load_catalog()["providers"]["nvidia_api"]
    assert provider["name"] == "NVIDIA NIM API"
    assert provider["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert provider["adapter"] == "nvidia_api"
    assert provider["adapter_available"] is True
    assert provider["registry_mode"] == "default"
    assert provider["runtime_enabled_by_default"] is True
    assert provider["credential_env"] == "NVIDIA_API_KEY"
    assert provider["discovery"] == {"enabled": True, "adapter": "nvidia"}
    assert ADAPTER_MODULES["nvidia_api"].is_file()
    assert (ROOT / "aurora" / "inference" / "providers" / "nvidia_discovery.py").is_file()


def test_only_existing_provider_adapters_are_marked_available():
    providers = load_catalog()["providers"]
    for provider_id, provider in providers.items():
        if provider["adapter_available"]:
            assert provider["adapter"] in ADAPTER_MODULES, provider_id
            assert ADAPTER_MODULES[provider["adapter"]].is_file(), provider_id
            assert provider["registry_mode"] != "catalog_only"
        else:
            assert provider["adapter"] is None, provider_id
            assert provider["registry_mode"] == "catalog_only", provider_id
            assert provider["runtime_enabled_by_default"] is False, provider_id


def test_registry_modes_match_current_runtime_wiring():
    providers = load_catalog()["providers"]
    assert {
        provider_id for provider_id, item in providers.items()
        if item["registry_mode"] == "default"
    } == {"nvidia_api"}
    assert {
        provider_id for provider_id, item in providers.items()
        if item["registry_mode"] == "credential_gated"
    } == {"openai", "anthropic", "openrouter"}


def test_catalog_never_contains_credentials_or_secret_values():
    catalog_text = CATALOG_PATH.read_text(encoding="utf-8")
    catalog = load_catalog()
    assert '"api_key"' not in catalog_text
    assert '"secret"' not in catalog_text.lower()
    for provider in catalog["providers"].values():
        assert "api_key" not in provider
        assert "secret" not in provider
