"""Redacted runtime failure visibility for Aurora.

Counters are process-local for fast health reporting; each transition is also
appended to a durable JSONL journal for post-restart diagnosis.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger("aurora.health")
_SECRET_PATTERN = re.compile(r"(nvapi-[A-Za-z0-9_-]+|Bearer\\s+[A-Za-z0-9._-]+|api[_-]?key\\s*[=:]\\s*[^\\s,;]+)", re.IGNORECASE)
_MAX_ERROR_CHARS = 300
AlertSink = Callable[[Mapping[str, Any]], None]


def _safe_error_text(error: BaseException) -> str:
    value = f"{type(error).__name__}: {error}"
    return _SECRET_PATTERN.sub("***REDACTED***", value)[:_MAX_ERROR_CHARS]


class FailureTracker:
    """Thread-safe counters with best-effort durable, redacted JSONL events."""

    def __init__(self, *, alert_threshold: int = 3, alert_cooldown_seconds: float = 900.0,
                 alert_sink: AlertSink | None = None, journal_path: str | None = None) -> None:
        self.alert_threshold = max(1, int(alert_threshold))
        self.alert_cooldown_seconds = max(0.0, float(alert_cooldown_seconds))
        self.alert_sink = alert_sink
        self.journal_path = Path(journal_path or os.getenv(
            "AURORA_FAILURE_JOURNAL", ".aurora-runtime/failure-events.jsonl"))
        self._lock = threading.RLock()
        self._state: dict[str, dict[str, Any]] = {}

    def _append(self, event: Mapping[str, Any]) -> None:
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(dict(event), sort_keys=True) + "\\n")
        except Exception as exc:
            # Do not print filesystem exception text: paths or environment may be sensitive.
            logger.error("failure journal append failed (%s)", type(exc).__name__)

    def record_failure(self, component: str, error: BaseException) -> int:
        safe_text = _safe_error_text(error)
        now = time.time()
        alert = None
        with self._lock:
            state = self._state.setdefault(component, {
                "consecutive_failures": 0, "total_failures": 0, "last_error": None,
                "last_failure_at": None, "last_success_at": None, "alerting": False,
                "_last_alert_monotonic": None,
            })
            state["consecutive_failures"] += 1
            state["total_failures"] += 1
            state["last_error"] = safe_text
            state["last_failure_at"] = now
            consecutive = state["consecutive_failures"]
            if consecutive >= self.alert_threshold:
                mono = time.monotonic()
                last = state["_last_alert_monotonic"]
                if last is None or mono - last >= self.alert_cooldown_seconds:
                    state["_last_alert_monotonic"] = mono
                    state["alerting"] = True
                    alert = {"kind": "failure_threshold", "component": component,
                             "consecutive_failures": consecutive, "total_failures": state["total_failures"],
                             "last_error": safe_text}
            snapshot = {k: v for k, v in state.items() if not k.startswith("_")}
        self._append({"event": "failure", "at": now, "component": component, **snapshot})
        logger.warning("swallowed failure in %s (consecutive=%d): %s", component, consecutive, safe_text)
        if alert is not None:
            logger.error("runtime failure threshold reached for %s (consecutive=%d)", component, consecutive)
            self._send(alert)
        return consecutive

    def record_success(self, component: str) -> None:
        now = time.time()
        with self._lock:
            state = self._state.setdefault(component, {
                "consecutive_failures": 0, "total_failures": 0, "last_error": None,
                "last_failure_at": None, "last_success_at": None, "alerting": False,
                "_last_alert_monotonic": None,
            })
            previous = state["consecutive_failures"]
            was_alerting = state["alerting"]
            state.update(consecutive_failures=0, alerting=False, last_success_at=now)
            state["_last_alert_monotonic"] = None
            snapshot = {k: v for k, v in state.items() if not k.startswith("_")}
        self._append({"event": "recovered", "at": now, "component": component,
                      "previous_consecutive_failures": previous, **snapshot})
        if was_alerting:
            self._send({"kind": "recovered", "component": component,
                        "previous_consecutive_failures": previous})

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: {k: v for k, v in state.items() if not k.startswith("_")}
                    for name, state in self._state.items()}

    def degraded_components(self) -> list[str]:
        with self._lock:
            return sorted(name for name, state in self._state.items() if state["alerting"])

    def _send(self, payload: Mapping[str, Any]) -> None:
        if self.alert_sink is None:
            return
        try:
            self.alert_sink(payload)
        except Exception as exc:
            logger.error("runtime alert sink failed (%s)", type(exc).__name__)


_default_tracker = FailureTracker()


def get_tracker() -> FailureTracker:
    return _default_tracker


def set_tracker(tracker: FailureTracker) -> FailureTracker:
    global _default_tracker
    previous = _default_tracker
    _default_tracker = tracker
    return previous


def report_failure(component: str, error: BaseException) -> int:
    return _default_tracker.record_failure(component, error)


def report_success(component: str) -> None:
    _default_tracker.record_success(component)


def configure_logging(level: str | None = None) -> None:
    root = logging.getLogger()
    if any(getattr(handler, "_aurora_handler", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler._aurora_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    chosen = (level or os.getenv("AURORA_LOG_LEVEL", "INFO")).strip().upper()
    root.setLevel(getattr(logging, chosen, logging.INFO))
