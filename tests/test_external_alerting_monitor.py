"""Tests for external alert delivery and the independent monitor."""
import json
from pathlib import Path

from aurora.alerts import ExternalAlertSink
from aurora.alert_monitor import MonitorState, run_check


def test_alert_sink_requires_proxy_and_allowlisted_telegram(monkeypatch, tmp_path):
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("AURORA_EGRESS_PROXY_PORT", "3128")
    monkeypatch.setenv("AURORA_ALERT_CHANNEL", "telegram")
    monkeypatch.setenv("AURORA_ALERT_TELEGRAM_BOT_TOKEN", "not-a-real-token")
    monkeypatch.setenv("AURORA_ALERT_TELEGRAM_CHAT_ID", "123")
    policy = tmp_path / "network-policy.json"
    policy.write_text(json.dumps({"allowed_hosts": ["api.telegram.org"]}), encoding="utf-8")
    sink = ExternalAlertSink(network_policy_path=str(policy))
    assert sink.channel == "telegram"
    assert sink.proxy_url == "http://127.0.0.1:3128"


def test_alert_sink_rejects_unlisted_telegram_host(monkeypatch, tmp_path):
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("AURORA_ALERT_CHANNEL", "telegram")
    monkeypatch.setenv("AURORA_ALERT_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("AURORA_ALERT_TELEGRAM_CHAT_ID", "123")
    policy = tmp_path / "network-policy.json"
    policy.write_text(json.dumps({"allowed_hosts": ["example.org"]}), encoding="utf-8")
    try:
        ExternalAlertSink(network_policy_path=str(policy))
    except RuntimeError as exc:
        assert "not allowlisted" in str(exc)
    else:
        raise AssertionError("unlisted alert host must be rejected")


def test_monitor_detects_down_service_and_deduplicates_across_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("AURORA_ALERT_COOLDOWN_SECONDS", "900")
    monkeypatch.setattr("aurora.alert_monitor._proxy_reachable", lambda: True)
    monkeypatch.setattr("aurora.alert_monitor.shutil.disk_usage",
                        lambda _path: type("Usage", (), {"used": 10, "total": 100})())
    state_path = tmp_path / "state.json"
    sent = []

    def failed_health():
        raise OSError("dashboard unavailable")

    first = run_check(health_fetcher=failed_health, sink=sent.append, state_path=state_path, now=1000)
    second = run_check(health_fetcher=failed_health, sink=sent.append, state_path=state_path, now=1060)
    assert first["health_ok"] is False
    assert "service_down" in first["conditions"]
    assert len([item for item in sent if item["kind"] == "service_down"]) == 1
    assert second["health_ok"] is False


def test_monitor_alerts_stuck_tasks_after_configured_duration(monkeypatch, tmp_path):
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("AURORA_ALERT_STUCK_MINUTES", "10")
    monkeypatch.setattr("aurora.alert_monitor._proxy_reachable", lambda: True)
    monkeypatch.setattr("aurora.alert_monitor.shutil.disk_usage",
                        lambda _path: type("Usage", (), {"used": 10, "total": 100})())
    state_path = tmp_path / "state.json"
    sent = []
    health = {
        "summary": {"stale_tasks": 2},
        "components": [{"key": "models", "status": "HEALTHY", "reason": "2 enabled, healthy and available model(s)."}],
    }
    run_check(health_fetcher=lambda: health, sink=sent.append, state_path=state_path, now=1000)
    result = run_check(health_fetcher=lambda: health, sink=sent.append, state_path=state_path, now=1661)
    assert "stuck_missions" in result["conditions"]
    assert any(item["kind"] == "stuck_missions" for item in sent)


def test_monitor_detects_no_models_disk_proxy_and_403_429(monkeypatch, tmp_path):
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("AURORA_ALERT_HTTP_ERROR_THRESHOLD", "2")
    monkeypatch.setenv("AURORA_MONITOR_DISK_PATH", str(tmp_path))
    monkeypatch.setattr("aurora.alert_monitor._proxy_reachable", lambda: False)
    monkeypatch.setattr("aurora.alert_monitor.shutil.disk_usage",
                        lambda _path: type("Usage", (), {"used": 90, "total": 100})())
    journal = tmp_path / "failures.jsonl"
    journal.write_text("\n".join([
        json.dumps({"at": 2000, "last_error": "HTTP Error 403: Forbidden"}),
        json.dumps({"at": 2001, "last_error": "HTTP Error 429: Too Many Requests"}),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setenv("AURORA_FAILURE_JOURNAL", str(journal))
    sent = []
    health = {
        "summary": {"stale_tasks": 0},
        "components": [{"key": "models", "status": "DEGRADED", "reason": "No enabled healthy and available model is currently registered."}],
    }
    result = run_check(health_fetcher=lambda: health, sink=sent.append,
                       state_path=tmp_path / "state.json", now=2002)
    assert {"no_models_available", "disk_high", "proxy_unreachable", "http_403_429_rate"} <= set(result["conditions"])
    assert len(sent) >= 4


def test_monitor_state_persists_cooldown(tmp_path):
    state = MonitorState(tmp_path / "state.json")
    sent = []
    payload = {"kind": "disk_high", "component": "disk"}
    assert state.alert("disk_high", payload, sent.append, cooldown_seconds=100, now=1000)
    assert not state.alert("disk_high", payload, sent.append, cooldown_seconds=100, now=1050)
    state.save()
    reloaded = MonitorState(tmp_path / "state.json")
    assert not reloaded.alert("disk_high", payload, sent.append, cooldown_seconds=100, now=1060)
    assert len(sent) == 1
