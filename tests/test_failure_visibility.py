"""Tests for redacted, durable runtime failure visibility."""
import json
import logging
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from aurora.dashboard.http_server import create_dashboard_server
from aurora.failure_tracker import FailureTracker, set_tracker
from aurora.orchestration.director_runtime import DirectorRuntime


def test_failure_counter_alerts_and_recovery_are_recorded(tmp_path):
    alerts = []
    tracker = FailureTracker(alert_threshold=2, alert_cooldown_seconds=900,
                             alert_sink=alerts.append, journal_path=str(tmp_path / "failures.jsonl"))
    assert tracker.record_failure("unit", RuntimeError("temporary failure")) == 1
    assert tracker.record_failure("unit", RuntimeError("temporary failure")) == 2
    assert len(alerts) == 1
    assert tracker.degraded_components() == ["unit"]
    tracker.record_success("unit")
    assert tracker.snapshot()["unit"]["consecutive_failures"] == 0
    assert tracker.degraded_components() == []
    events = [json.loads(line) for line in (tmp_path / "failures.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["failure", "failure", "recovered"]


def test_error_secrets_are_redacted_from_snapshot_and_journal(tmp_path):
    journal = tmp_path / "failures.jsonl"
    alerts = []
    tracker = FailureTracker(alert_threshold=1, alert_sink=alerts.append, journal_path=str(journal))
    tracker.record_failure("unit", RuntimeError("request failed Bearer abc.DEF-123 and nvapi-SECRETKEY999"))
    snapshot = tracker.snapshot()["unit"]["last_error"]
    stored = journal.read_text()
    rendered_alerts = repr(alerts)
    assert "abc.DEF-123" not in snapshot + stored + rendered_alerts
    assert "SECRETKEY999" not in snapshot + stored + rendered_alerts
    assert "REDACTED" in snapshot + stored + rendered_alerts


def test_failure_journal_write_failure_does_not_break_runtime(tmp_path, caplog):
    # A directory used as the journal file forces an append error.
    directory = tmp_path / "journal"
    directory.mkdir()
    tracker = FailureTracker(journal_path=str(directory))
    with caplog.at_level(logging.ERROR, logger="aurora.health"):
        assert tracker.record_failure("unit", RuntimeError("boom")) == 1
    assert "failure journal append failed" in caplog.text


def test_alert_sink_failure_does_not_break_runtime(tmp_path, caplog):
    def broken_sink(_payload):
        raise RuntimeError("sensitive internal detail")

    tracker = FailureTracker(alert_threshold=1, alert_sink=broken_sink,
                             journal_path=str(tmp_path / "failures.jsonl"))
    with caplog.at_level(logging.ERROR, logger="aurora.health"):
        assert tracker.record_failure("unit", RuntimeError("boom")) == 1
    assert "runtime alert sink failed" in caplog.text
    assert "sensitive internal detail" not in caplog.text


@pytest.fixture
def installed_failure_tracker(tmp_path):
    tracker = FailureTracker(
        alert_threshold=3,
        alert_cooldown_seconds=900,
        journal_path=str(tmp_path / "runtime-failures.jsonl"),
    )
    previous = set_tracker(tracker)
    try:
        yield tracker
    finally:
        set_tracker(previous)


def test_catalog_refresh_failures_are_tracked_and_recovery_resets_counter(installed_failure_tracker):
    class Discovery:
        fail = True

        def refresh_all(self, **_kwargs):
            if self.fail:
                raise RuntimeError("catalog unreachable")

        def activate_free_discovered(self, **_kwargs):
            return None

    class EventBus:
        def __init__(self):
            self.events = []

        def publish(self, **event):
            self.events.append(event)

    discovery = Discovery()
    event_bus = EventBus()
    runtime = SimpleNamespace(
        model_discovery_refresh_enabled=True,
        model_discovery=discovery,
        autonomous_discovery_refresh=None,
        _last_model_discovery_refresh_at=None,
        model_discovery_refresh_interval_seconds=900.0,
        _model_catalog_refresh_consecutive_failures=0,
        event_bus=event_bus,
    )
    for _ in range(3):
        runtime._last_model_discovery_refresh_at = None
        DirectorRuntime._refresh_model_catalogs_if_due(runtime)

    assert installed_failure_tracker.snapshot()["model_catalog_refresh"]["consecutive_failures"] == 3
    assert sum(event["event_type"] == "model_catalog_refresh_failed" for event in event_bus.events) == 3
    assert any(event["event_type"] == "model_catalog_refresh_alert" for event in event_bus.events)

    discovery.fail = False
    runtime._last_model_discovery_refresh_at = None
    DirectorRuntime._refresh_model_catalogs_if_due(runtime)
    assert installed_failure_tracker.snapshot()["model_catalog_refresh"]["consecutive_failures"] == 0
    assert any(event["event_type"] == "model_catalog_refresh_recovered" for event in event_bus.events)
    journal = (installed_failure_tracker.journal_path).read_text(encoding="utf-8")
    assert '"event": "failure"' in journal and '"event": "recovered"' in journal


def test_health_endpoint_exposes_runtime_failure_snapshot(installed_failure_tracker):
    installed_failure_tracker.record_failure("observability_record", RuntimeError("disk full"))
    server, base = _serve(SimpleNamespace(health=lambda: {"status": "ok"}))
    try:
        with urlopen(base + "/api/dashboard/health", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["runtime_failures"]["observability_record"]["consecutive_failures"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_internal_error_details_stay_out_of_http_response_and_logs(caplog):
    def broken_health():
        raise RuntimeError("sensitive internal detail")

    caplog.set_level(logging.ERROR, logger="aurora.dashboard")
    server, base = _serve(SimpleNamespace(health=broken_health))
    try:
        with pytest.raises(HTTPError) as caught:
            urlopen(base + "/api/dashboard/health", timeout=5)
        assert caught.value.code == 500
        assert "sensitive internal detail" not in caught.value.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

    assert "dashboard request failed" in caplog.text
    assert "sensitive internal detail" not in caplog.text


def _serve(health_service):
    server = create_dashboard_server(
        dashboard_gateway=SimpleNamespace(snapshot=lambda **_: {}),
        conversation_api=object(),
        health_service=health_service,
        host="127.0.0.1",
        port=0,
        ui_root=Path(__file__).parents[1] / "aurora" / "dashboard" / "ui",
    )
    Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:" + str(server.server_address[1])
