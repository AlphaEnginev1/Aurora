from __future__ import annotations

import json

from aurora.operator_settings import OperatorSettingsStore, _normalize


LEGACY_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


def test_router_mode_never_pins_historical_nvidia_model() -> None:
    normalized = _normalize(
        {
            "director": {
                "provider": "router",
                "model": LEGACY_MODEL,
            }
        }
    )

    assert normalized["director"]["provider"] == "router"
    assert normalized["director"]["model"] == "router"


def test_router_mode_migrates_legacy_persisted_model_on_load(tmp_path) -> None:
    path = tmp_path / "operator-settings.json"
    path.write_text(
        json.dumps(
            {
                "director": {
                    "provider": "router",
                    "model": LEGACY_MODEL,
                }
            }
        ),
        encoding="utf-8",
    )

    settings = OperatorSettingsStore(path)
    loaded = settings.load()

    assert loaded["director"]["provider"] == "router"
    assert loaded["director"]["model"] == "router"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["director"]["model"] == "router"


def test_direct_nvidia_mode_keeps_explicit_nvidia_model() -> None:
    model = "nvidia/some-dynamically-discovered-model"
    normalized = _normalize(
        {
            "director": {
                "provider": "nvidia_api",
                "model": model,
            }
        }
    )

    assert normalized["director"]["provider"] == "nvidia_api"
    assert normalized["director"]["model"] == model
