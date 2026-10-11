"""One-shot external health monitor intended to run from a systemd timer every minute."""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
from typing import Any, Callable, Mapping

from aurora.alerts import ExternalAlertSink

logger = logging.getLogger("aurora.alert_monitor")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _health_url() -> str:
    return os.getenv("AURORA_MONITOR_HEALTH_URL", "http://127.0.0.1:8787/api/dashboard/health")


def fetch_health(url: str | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    token = os.getenv("AURORA_DASHBOARD_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url or _health_url(), headers=headers)
    # The health endpoint is local; bypass the remote egress proxy for loopback only.\n    opener = build_opener(ProxyHandler({}))\n    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise URLError(f"health endpoint returned HTTP {response.status}")
        payload = json.loads(response.read(2_000_000).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("health endpoint returned a non-object payload")
    return payload


class MonitorState:
    """Small atomic JSON state for dedupe and duration tracking across timer runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def save(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def alert(self, key: str, payload: Mapping[str, Any], sink: Callable[[Mapping[str, Any]], None],
              *, cooldown_seconds: float, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        sent = self.data.setdefault("last_alert_at", {})
        previous = sent.get(key)
        if previous is not None and moment - float(previous) < max(0.0, cooldown_seconds):
            return False
        try:
            sink(payload)
        except Exception as exc:
            # Persist cooldown only after a successful delivery; logs never include payloads or secrets.
            logger.error("external alert delivery failed (%s)", type(exc).__name__)
            return False
        sent[key] = moment
        return True


def _proxy_reachable() -> bool:
    host = os.getenv("AURORA_EGRESS_PROXY_HOST", "").strip()
    try:
        port = int(os.getenv("AURORA_EGRESS_PROXY_PORT", "3128"))
        if not host or not 1 <= port <= 65535:
            return False
        with socket.create_connection((host, port), timeout=2):
            return True
    except (OSError, ValueError):
        return False


def _recent_http_errors(journal: Path, *, now: float, window_seconds: int = 600) -> int:
    count = 0
    try:
        with journal.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timestamp = event.get("at")
                if not isinstance(timestamp, (int, float)) or now - timestamp > window_seconds:
                    continue
                message = str(event.get("last_error", ""))
                if "403" in message or "429" in message:
                    count += 1
    except OSError:
        pass
    return count


def run_check(*, health_fetcher: Callable[[], dict[str, Any]] = fetch_health,
              sink: Callable[[Mapping[str, Any]], None] | None = None,
              state_path: str | Path | None = None,
              now: float | None = None) -> dict[str, Any]:
    """Evaluate health and send deduplicated alerts; does not mutate Aurora runtime state."""
    moment = time.time() if now is None else now
    state = MonitorState(state_path or os.getenv(
        "AURORA_MONITOR_STATE_FILE", ".aurora-runtime/alert-monitor-state.json"))
    alert_sink = sink or ExternalAlertSink()
    cooldown = max(60.0, float(os.getenv("AURORA_ALERT_COOLDOWN_SECONDS", "900")))
    stuck_minutes = max(1.0, float(os.getenv("AURORA_ALERT_STUCK_MINUTES", "10")))
    disk_threshold = min(100.0, max(1.0, float(os.getenv("AURORA_ALERT_DISK_PERCENT", "80"))))
    journal = Path(os.getenv("AURORA_FAILURE_JOURNAL", ".aurora-runtime/failure-events.jsonl"))
    conditions: dict[str, dict[str, Any]] = {}
    health: dict[str, Any] | None = None
    try:
        health = health_fetcher()
    except Exception as exc:
        conditions["service_down"] = {
            "kind": "service_down", "component": "dashboard_health",
            "message": f"Health endpoint did not respond ({type(exc).__name__})",
        }

    proxy_ok = _proxy_reachable()
    if not proxy_ok:
        conditions["proxy_unreachable"] = {
            "kind": "proxy_unreachable", "component": "egress_proxy",
            "message": "Configured Aurora egress proxy is unreachable; external delivery may also fail.",
        }

    usage = shutil.disk_usage(os.getenv("AURORA_MONITOR_DISK_PATH", "."))
    disk_percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
    if disk_percent >= disk_threshold:
        conditions["disk_high"] = {
            "kind": "disk_high", "component": "disk",
            "message": f"Disk usage is {disk_percent:.1f}% (threshold {disk_threshold:.1f}%).",
        }

    if health is not None:
        components = health.get("components", [])
        model_component = next((item for item in components if item.get("key") == "models"), {})
        if model_component.get("status") in {"CRITICAL", "DEGRADED"} and (
            "no enabled healthy" in str(model_component.get("reason", "")).lower()
            or "0 enabled" in str(model_component.get("reason", "")).lower()
        ):
            conditions["no_models_available"] = {
                "kind": "no_models_available", "component": "model_layer",
                "message": str(model_component.get("reason", "No usable model is available."))[:300],
            }

        stale = int(health.get("summary", {}).get("stale_tasks", 0) or 0)
        first_seen = state.data.setdefault("condition_first_seen", {})
        if stale > 0:
            first_seen.setdefault("stuck_missions", moment)
            if moment - float(first_seen["stuck_missions"]) >= stuck_minutes * 60:
                conditions["stuck_missions"] = {
                    "kind": "stuck_missions", "component": "task_runtime",
                    "message": f"{stale} task(s) have expired leases for at least {stuck_minutes:g} minutes.",
                }
        else:
            first_seen.pop("stuck_missions", None)

    http_error_count = _recent_http_errors(journal, now=moment)
    threshold = max(1, int(os.getenv("AURORA_ALERT_HTTP_ERROR_THRESHOLD", "5")))
    if http_error_count >= threshold:
        conditions["http_403_429_rate"] = {
            "kind": "http_403_429_rate", "component": "provider_http",
            "message": f"{http_error_count} HTTP 403/429 failures recorded in the last 10 minutes.",
        }

    previous_active = set(state.data.get("active_conditions", []))
    current_active = set(conditions)
    for key, payload in conditions.items():
        state.alert(key, payload, alert_sink, cooldown_seconds=cooldown, now=moment)
    for key in sorted(previous_active - current_active):
        state.alert(f"recovered:{key}", {
            "kind": "recovered", "component": key,
            "message": f"Condition {key} has cleared.",
        }, alert_sink, cooldown_seconds=cooldown, now=moment)
    state.data["active_conditions"] = sorted(current_active)
    state.data["last_check_at"] = _utc_now()
    state.data["last_health_ok"] = health is not None
    state.save()
    return {
        "health_ok": health is not None,
        "conditions": sorted(current_active),
        "http_403_429_count": http_error_count,
        "disk_percent": round(disk_percent, 2),
        "proxy_reachable": proxy_ok,
    }


def main() -> int:
    import argparse
    import logging
    parser = argparse.ArgumentParser(description="Check Aurora health and deliver external alerts")
    parser.add_argument("--once", action="store_true", help="Run one check and exit (systemd timer mode)")
    parser.add_argument("--interval", type=float, default=60.0, help="Continuous check interval in seconds")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("AURORA_LOG_LEVEL", "INFO").upper())
    if args.once:
        result = run_check()
        print(json.dumps(result, sort_keys=True))
        return 0 if result["health_ok"] else 1
    while True:
        try:
            run_check()
        except Exception as exc:
            logger.error("monitor check failed (%s)", type(exc).__name__)
        time.sleep(max(10.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
