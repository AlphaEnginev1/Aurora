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
    assert set(raw["providers"]) == {"nvidia_api"}
    assert raw["providers"]["nvidia_api"]["api_key"] == "nvidia-secret"
    assert raw["director"]["provider"] == "router"
    assert public["providers"]["nvidia_api"]["api_key_present"] is True
    assert "nvidia-secret" not in str(public)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_blank_form_key_preserves_existing_secret(tmp_path: Path):
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    store.save({"providers": {"nvidia_api": {"api_key": "existing-nvidia"}}})
    store.update_from_form({"providers": {"nvidia_api": {"api_key": ""}}})
    assert store.credential("nvidia_api") == "existing-nvidia"
