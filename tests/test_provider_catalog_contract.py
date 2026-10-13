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
VALID_REGISTRY_MODES = {"default", "include_external", "include_openrouter", "catalog_only"}
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


def test_settings_provider_selector_is_rendered_from_catalog_api():
    html = UI_PATH.read_text(encoding="utf-8")
    js_path = ROOT / "aurora" / "dashboard" / "ui" / "provider_credentials.js"
    js = js_path.read_text(encoding="utf-8")
    service_path = ROOT / "aurora" / "dashboard" / "provider_settings.py"
    service = service_path.read_text(encoding="utf-8")

    assert '<select id="credential-provider"' in html
    assert "data.provider_catalog" in js
    assert "provider.provider_id" in js
    assert "provider.name" in js
    assert "metadata.base_url" in js
    assert 'data["provider_catalog"] = list(self.catalog.providers)' in service


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
        if item["registry_mode"] == "include_external"
    } == {"openai", "anthropic"}
    assert {
        provider_id for provider_id, item in providers.items()
        if item["registry_mode"] == "include_openrouter"
    } == {"openrouter"}


def test_catalog_never_contains_credentials_or_secret_values():
    catalog_text = CATALOG_PATH.read_text(encoding="utf-8")
    catalog = load_catalog()
    assert '"api_key"' not in catalog_text
    assert '"secret"' not in catalog_text.lower()
    for provider in catalog["providers"].values():
        assert "api_key" not in provider
        assert "secret" not in provider



def test_provider_catalog_reader_loads_from_repository_and_resolves_entries():
    from aurora.inference.providers.catalog import ProviderCatalog

    catalog = ProviderCatalog(CATALOG_PATH)
    assert "nvidia_api" in catalog.provider_ids
    assert "openrouter" in catalog.provider_ids
    assert catalog.resolve("nvidia_api")["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert all("api_key" not in item for item in catalog.providers)


def test_provider_catalog_rejects_unknown_provider_ids(tmp_path: Path):
    import pytest
    from aurora.inference.providers.catalog import ProviderCatalog, ProviderCatalogError

    catalog = ProviderCatalog(CATALOG_PATH)
    with pytest.raises(ProviderCatalogError, match="not in catalog"):
        catalog.resolve("not_configured")


def test_provider_catalog_rejects_invalid_schema(tmp_path: Path):
    import pytest
    from aurora.inference.providers.catalog import ProviderCatalog, ProviderCatalogError

    path = tmp_path / "providers.json"
    path.write_text('{"schema_version": 99, "providers": {}}', encoding="utf-8")
    with pytest.raises(ProviderCatalogError, match="schema_version must be 1"):
        ProviderCatalog(path)


def test_default_registry_is_catalog_driven_and_keeps_activation_gates():
    from aurora.inference.providers.catalog import ProviderCatalog
    from aurora.inference.providers.registry import default_provider_registry

    catalog = ProviderCatalog(CATALOG_PATH)
    default = default_provider_registry(nvidia_api_key="nvidia-test", catalog=catalog)
    external = default_provider_registry(
        nvidia_api_key="nvidia-test",
        openai_api_key="openai-test",
        anthropic_api_key="anthropic-test",
        include_external=True,
        catalog=catalog,
    )
    openrouter = default_provider_registry(
        nvidia_api_key="nvidia-test",
        openrouter_api_key="openrouter-test",
        include_openrouter=True,
        catalog=catalog,
    )

    assert default.ids == ("nvidia_api",)
    assert external.ids == ("anthropic", "nvidia_api", "openai")
    assert openrouter.ids == ("nvidia_api", "openrouter")


def test_registry_fails_closed_for_unapproved_catalog_adapter(tmp_path: Path):
    import pytest
    from aurora.inference.providers.catalog import ProviderCatalog
    from aurora.inference.providers.registry import ProviderRegistryError, default_provider_registry

    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    data["providers"]["openai"]["adapter"] = "arbitrary_python_import"
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    catalog = ProviderCatalog(path)

    with pytest.raises(ProviderRegistryError, match="unapproved adapter"):
        default_provider_registry(
            nvidia_api_key="nvidia-test",
            openai_api_key="openai-test",
            include_external=True,
            catalog=catalog,
        )
