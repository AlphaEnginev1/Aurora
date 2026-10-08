"""Explicit migrations for persisted Aurora operator settings.

Runtime construction must remain side-effect free with respect to operator
configuration. These migrations are intentionally callable as a separate,
operator-controlled step.
"""

from __future__ import annotations

from typing import Any

from .operator_settings import OperatorSettingsStore


def migrate_legacy_director_provider(settings_store: OperatorSettingsStore) -> bool:
    """Migrate a persisted direct-provider Director selection to Router mode.

    Returns True only when a legacy direct-provider selection was changed.
    The migration is deliberately explicit and is never invoked by runtime
    construction.
    """
    current = settings_store.load()
    director = current.get("director", {}) if isinstance(current, dict) else {}
    provider = str(director.get("provider", "router")).strip().lower()
    if provider == "router":
        return False

    settings_store.save(
        {
            "director": {
                "provider": "router",
                "model": "router",
            }
        }
    )
    return True


__all__ = ["migrate_legacy_director_provider"]
