"""Tests for redacted, durable runtime failure visibility."""
import json
import logging

from aurora.failure_tracker import FailureTracker


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
    tracker = FailureTracker(journal_path=str(journal))
    tracker.record_failure("unit", RuntimeError("request failed Bearer abc.DEF-123 and nvapi-SECRETKEY999"))
    snapshot = tracker.snapshot()["unit"]["last_error"]
    stored = journal.read_text()
    assert "abc.DEF-123" not in snapshot + stored
    assert "SECRETKEY999" not in snapshot + stored
    assert "REDACTED" in snapshot + stored


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
