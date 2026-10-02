from pathlib import Path
import os

from aurora.operator_settings import OperatorSettingsStore


def test_operator_settings_round_trip_redacts_credentials(tmp_path: Path):
    path = tmp_path / ".aurora-runtime" / "operator-settings.json"
    store = OperatorSettingsStore(path)
    store.save({
        "director": {
            "provider": "gemini",
            "model": "gemini-3.7-flash",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta",
        },
        "providers": {
            "gemini": {"api_key": "gemini-secret"},
            "nvidia_api": {"api_key": "nvidia-secret"},
        },
    })

    raw = store.load()
    public = store.public()

    assert raw["providers"]["gemini"]["api_key"] == "gemini-secret"
    assert raw["providers"]["nvidia_api"]["api_key"] == "nvidia-secret"
    assert public["providers"]["gemini"]["api_key_present"] is True
    assert public["providers"]["nvidia_api"]["api_key_present"] is True
    assert "gemini-secret" not in str(public)
    assert "nvidia-secret" not in str(public)
    assert "api_key" not in public["providers"]["gemini"]
    assert "api_key" not in public["providers"]["nvidia_api"]
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_blank_form_key_preserves_existing_secret(tmp_path: Path):
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({
        "providers": {
            "gemini": {"api_key": "existing"},
            "nvidia_api": {"api_key": "existing-nvidia"},
        }
    })
    store.update_from_form({
        "providers": {
            "gemini": {"api_key": ""},
            "nvidia_api": {"api_key": ""},
        }
    })
    assert store.credential("gemini") == "existing"
    assert store.credential("nvidia_api") == "existing-nvidia"
