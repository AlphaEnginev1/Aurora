from pathlib import Path
import os

from aurora.operator_settings import OperatorSettingsStore


def test_operator_settings_round_trip_redacts_credentials(tmp_path: Path):
    path = tmp_path / ".aurora-runtime" / "operator-settings.json"
    store = OperatorSettingsStore(path)
    store.save({
        "director": {
            "provider": "router",
            "model": "nvidia/nemotron-3-ultra-550b-a55b",
            "endpoint": "https://integrate.api.nvidia.com/v1",
        },
        "providers": {
            "nvidia_api": {"api_key": "nvidia-secret"},
            "legacy_provider": {"api_key": "ignored"},
        },
    })
    raw = store.load()
    public = store.public()
    assert set(raw["providers"]) == {"nvidia_api", "legacy_provider"}
    assert raw["providers"]["nvidia_api"]["api_key"] == "nvidia-secret"
    assert raw["director"]["provider"] == "router"
    assert public["providers"]["nvidia_api"]["api_key_present"] is True
    assert public["providers"]["legacy_provider"]["api_key_present"] is True
    assert "api_key" not in public["providers"]["nvidia_api"]
    assert "nvidia-secret" not in str(public)
    assert "ignored" not in str(public)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_blank_form_key_preserves_existing_secret(tmp_path: Path):
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({"providers": {"nvidia_api": {"api_key": "existing-nvidia"}}})
    store.update_from_form({"providers": {"nvidia_api": {"api_key": ""}}})
    assert store.credential("nvidia_api") == "existing-nvidia"


def test_legacy_external_model_is_migrated_to_router_contract(tmp_path: Path):
    path = tmp_path / "operator-settings.json"
    path.write_text(
        """{
          "director": {
            "provider": "gemini",
            "model": "gemini-3.8-flash",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta",
            "failover": {"enabled": true, "order": ["gemini", "nvidia_api"]}
          },
          "providers": {
            "gemini": {"enabled": true, "api_key": "legacy-secret"},
            "nvidia_api": {"enabled": true, "api_key": "nvidia-secret", "base_url": "https://integrate.api.nvidia.com/v1"}
          }
        }""",
        encoding="utf-8",
    )
    store = OperatorSettingsStore(path)
    loaded = store.load()
    assert loaded["director"]["provider"] == "router"
    assert loaded["director"]["model"] == "router"
    assert loaded["director"]["endpoint"] == "https://integrate.api.nvidia.com/v1"


def test_multiple_provider_credentials_are_stored_and_redacted(tmp_path: Path):
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.update_from_form({
        "providers": {
            "openai": {
                "api_key": "openai-private",
                "base_url": "https://api.openai.com/v1",
            },
            "anthropic": {
                "api_key": "anthropic-private",
                "base_url": "https://api.anthropic.com/v1",
            },
            "xai": {
                "api_key": "grok-private",
                "base_url": "https://api.x.ai/v1",
            },
        },
    })

    public = store.public()
    assert store.credential("openai") == "openai-private"
    assert store.credential("anthropic") == "anthropic-private"
    assert store.credential("xai") == "grok-private"
    assert public["providers"]["openai"]["api_key_present"] is True
    assert public["providers"]["anthropic"]["api_key_present"] is True
    assert public["providers"]["xai"]["api_key_present"] is True
    assert "openai-private" not in str(public)
    assert "anthropic-private" not in str(public)
    assert "grok-private" not in str(public)


def test_blank_key_for_one_provider_preserves_existing_key(tmp_path: Path):
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({"providers": {"openrouter": {
        "api_key": "existing-openrouter",
        "base_url": "https://openrouter.ai/api/v1",
    }}})
    store.update_from_form({"providers": {"openrouter": {"api_key": ""}}})
    assert store.credential("openrouter") == "existing-openrouter"


def test_provider_endpoint_rejects_non_loopback_http_even_if_hostname_starts_with_localhost(tmp_path: Path):
    import pytest
    from aurora.operator_settings import OperatorSettingsError

    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    with pytest.raises(OperatorSettingsError, match="must use HTTPS"):
        store.save({
            "providers": {
                "ollama": {
                    "base_url": "http://127.0.0.1.attacker.example/v1",
                },
            },
        })


def test_provider_endpoint_rejects_embedded_credentials(tmp_path: Path):
    import pytest
    from aurora.operator_settings import OperatorSettingsError

    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    with pytest.raises(OperatorSettingsError, match="must not contain embedded credentials"):
        store.save({
            "providers": {
                "openai": {
                    "api_key": "not-the-url-password",
                    "base_url": "https://user:password@api.openai.com/v1",
                },
            },
        })


def test_local_ollama_http_endpoint_is_allowed_for_loopback_only(tmp_path: Path):
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "ollama": {
                "base_url": "http://127.0.0.1:11434/v1",
            },
        },
    })
    assert store.load()["providers"]["ollama"]["base_url"] == "http://127.0.0.1:11434/v1"

def test_director_failover_order_allows_only_explicitly_approved_free_providers(tmp_path: Path):
    import pytest
    from aurora.operator_settings import OperatorSettingsError

    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    saved = store.save({
        "director": {
            "failover": {"enabled": True, "order": ["openrouter", "nvidia_api"]},
        },
        "providers": {
            "openrouter": {
                "api_key": "openrouter-test",
                "base_url": "https://openrouter.ai/api/v1",
            },
        },
    })
    assert saved["director"]["failover"]["order"] == ["openrouter", "nvidia_api"]
    assert store.load()["director"]["failover"]["order"] == ["openrouter", "nvidia_api"]

    with pytest.raises(OperatorSettingsError, match="approved free-only providers"):
        store.save({
            "director": {
                "failover": {"enabled": True, "order": ["openrouter", "openai"]},
            },
        })


def test_openrouter_director_setting_is_pinned_to_official_free_route(tmp_path: Path):
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    saved = store.save({
        "director": {
            "provider": "openrouter",
            "model": "any-model-from-form",
        },
        "providers": {
            "openrouter": {
                "api_key": "openrouter-test",
                "base_url": "https://openrouter.ai/api/v1",
            },
        },
    })
    assert saved["director"]["provider"] == "openrouter"
    assert saved["director"]["model"] == "openrouter/free"


def test_openrouter_director_setting_requires_a_credential(tmp_path: Path, monkeypatch):
    import pytest
    from aurora.operator_settings import OperatorSettingsError

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    with pytest.raises(OperatorSettingsError, match="requires an OpenRouter API key"):
        store.save({"director": {"provider": "openrouter"}})

