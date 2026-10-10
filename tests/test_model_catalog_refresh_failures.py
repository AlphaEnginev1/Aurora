from __future__ import annotations

import logging

from aurora.orchestration.director_runtime import DirectorRuntime


class _Discovery:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = 0

    def refresh_all(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error

    def activate_free_discovered(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error


class _Bus:
    def __init__(self):
        self.events = []

    def publish(self, **kwargs):
        self.events.append(kwargs)


def _runtime(discovery: _Discovery) -> DirectorRuntime:
    runtime = DirectorRuntime.__new__(DirectorRuntime)
    runtime.model_discovery_refresh_enabled = True
    runtime.model_discovery = discovery
    runtime.autonomous_discovery_refresh = None
    runtime.model_discovery_refresh_interval_seconds = 0
    runtime._last_model_discovery_refresh_at = 0
    runtime._model_catalog_refresh_consecutive_failures = 0
    runtime.event_bus = _Bus()
    return runtime


def test_catalog_refresh_failure_is_logged_and_counted(caplog):
    discovery = _Discovery(RuntimeError("catalog endpoint unavailable"))
    runtime = _runtime(discovery)

    with caplog.at_level(logging.ERROR):
        runtime._refresh_model_catalogs_if_due()

    assert runtime._model_catalog_refresh_consecutive_failures == 1
    assert "NVIDIA model catalog refresh failed" in caplog.text
    assert any(
        event["event_type"] == "model_catalog_refresh_failed"
        and event["payload"]["consecutive_failures"] == 1
        for event in runtime.event_bus.events
    )


def test_catalog_refresh_emits_alert_at_three_consecutive_failures():
    runtime = _runtime(_Discovery(RuntimeError("catalog unavailable")))

    for _ in range(3):
        runtime._refresh_model_catalogs_if_due()

    event_types = [event["event_type"] for event in runtime.event_bus.events]
    assert event_types.count("model_catalog_refresh_failed") == 3
    assert event_types.count("model_catalog_refresh_alert") == 1
    assert runtime._model_catalog_refresh_consecutive_failures == 3


def test_catalog_refresh_success_resets_failure_counter_and_records_recovery():
    runtime = _runtime(_Discovery(RuntimeError("temporary outage")))
    runtime._refresh_model_catalogs_if_due()
    runtime.model_discovery = _Discovery()

    runtime._refresh_model_catalogs_if_due()

    assert runtime._model_catalog_refresh_consecutive_failures == 0
    assert any(
        event["event_type"] == "model_catalog_refresh_recovered"
        and event["payload"]["previous_consecutive_failures"] == 1
        for event in runtime.event_bus.events
    )
