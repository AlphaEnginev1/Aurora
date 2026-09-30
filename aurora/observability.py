"""Production observability primitives for Aurora.

Observability is intentionally observational: it records execution context and
outcomes without becoming authoritative for Director decisions or learning
policy.
"""

from __future__ import annotations

import fcntl
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ObservabilitySecurityError(ValueError):
    """Raised when an observability payload contains credential material."""


_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_\-])(?:authorization|password|passwd|secret|credential|"
    r"api[_\-]?key|access[_\-]?token|refresh[_\-]?token|private[_\-]?key|"
    r"client[_\-]?secret|token)(?:$|[_\-])",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bBasic\s+[A-Za-z0-9+/=]+", re.IGNORECASE),
)


def _contains_credential_material(value: Any, *, key: str | None = None) -> bool:
    if key is not None and _SENSITIVE_KEY_RE.search(key):
        return True

    if isinstance(value, dict):
        return any(
            _contains_credential_material(child, key=str(child_key))
            for child_key, child in value.items()
        )

    if isinstance(value, (list, tuple, set)):
        return any(_contains_credential_material(child) for child in value)

    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS)

    return False


@dataclass(slots=True)
class ObservabilityEvent:
    event_type: str
    correlation_id: str
    mission_id: str | None = None
    decision_id: str | None = None
    task_id: str | None = None
    execution_id: str | None = None
    request_id: str | None = None
    component: str | None = None
    agent_id: str | None = None
    role: str | None = None
    provider: str | None = None
    model: str | None = None
    status: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: float | None = None
    error_code: str | None = None
    retryable: bool | None = None
    result: Any = None
    evaluation: Any = None
    evidence: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: str = "PRODUCTION-OBSERVABILITY-V1"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-serializable event payload."""
        payload = asdict(self)
        if _contains_credential_material(payload):
            raise ObservabilitySecurityError(
                "credential material is not permitted in observability events"
            )
        return payload


class ProductionObservability:
    """Durable JSONL event recorder and cycle reconstruction boundary."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: ObservabilityEvent) -> ObservabilityEvent:
        if not isinstance(event, ObservabilityEvent):
            raise TypeError("event must be an ObservabilityEvent")

        payload = event.to_dict()
        line = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line + "\n")
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        return event

    def reconstruct_cycle(self, correlation_id: str) -> list[ObservabilityEvent]:
        if not self.path.exists():
            return []

        events: list[ObservabilityEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if payload.get("correlation_id") != correlation_id:
                    continue
                events.append(ObservabilityEvent(**payload))

        return events

    def observe_decision(
        self,
        *,
        correlation_id: str,
        decision_id: str,
        result: Any,
    ) -> Any:
        """Record an observation while returning the authoritative result untouched."""
        self.record(
            ObservabilityEvent(
                event_type="decision_completed",
                correlation_id=correlation_id,
                decision_id=decision_id,
                component="Observability",
                status="observed",
                result=result,
            )
        )
        return result
