"""Production observability primitives for Aurora.

The observability layer is deliberately non-authoritative: it reconstructs
durable runtime activity without changing Director decisions, task state,
routing, or learning policy.
"""

from __future__ import annotations

import fcntl
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


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
    if isinstance(value, Mapping):
        return any(
            _contains_credential_material(child, key=str(child_key))
            for child_key, child in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_credential_material(child) for child in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS)
    return False


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(started_at: str | None, completed_at: str | None) -> float | None:
    started = _parse_timestamp(started_at)
    completed = _parse_timestamp(completed_at)
    if started is None or completed is None:
        return None
    return max(0.0, (completed - started).total_seconds() * 1000.0)


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
        payload = asdict(self)
        if _contains_credential_material(payload):
            raise ObservabilitySecurityError(
                "credential material is not permitted in observability events"
            )
        return payload


class ProductionObservability:
    """Durable JSONL recorder plus bridge from Aurora's durable task events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _event_exists_locked(self, event_id: str) -> bool:
        if not self.path.exists():
            return False
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    if json.loads(line).get("event_id") == event_id:
                        return True
                except json.JSONDecodeError:
                    continue
        return False

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
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                if not self._event_exists_locked(event.event_id):
                    handle.seek(0, 2)
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

    def record_mission_events(
        self,
        events: Sequence[Any],
        *,
        task_results: Mapping[str, Any] | None = None,
    ) -> tuple[ObservabilityEvent, ...]:
        """Translate durable Aurora events into canonical V1 observations.

        The source TaskEvent IDs are retained in metadata, making ingestion
        idempotent across repeated runtime cycles and process restarts.
        """
        source_events = tuple(events)
        if not source_events:
            return ()

        task_results = task_results or {}
        decisions_by_round: dict[int, str] = {}
        review_by_id: dict[int, Any] = {}
        latest_decision_id: str | None = None
        task_started_at: dict[str, str] = {}
        for source in source_events:
            if source.event_type != "director_review":
                continue
            round_number = source.payload.get("round")
            if isinstance(round_number, int):
                decisions_by_round[round_number] = f"decision:{source.event_id}"
            latest_decision_id = f"decision:{source.event_id}"
            review_by_id[int(source.event_id)] = source

        observations: list[ObservabilityEvent] = []
        for source in source_events:
            payload = dict(source.payload or {})
            task_id = source.task_id
            stored = task_results.get(task_id) if task_id else None
            result = getattr(stored, "result", None) if stored is not None else None
            task = getattr(stored, "task", None) if stored is not None else None

            provider = None
            model = None
            request_id = None
            execution_id = None
            routing = result.get("routing") if isinstance(result, dict) else None
            if isinstance(result, dict):
                provider = result.get("provider")
                model = result.get("reported_model") or result.get("model")
                request_id = result.get("request_id")
                execution_id = result.get("execution_id")
                if not model and isinstance(routing, dict):
                    model = routing.get("selected_model")

            if task_id and not execution_id:
                attempt = payload.get("attempt")
                if attempt is None and stored is not None:
                    attempt = getattr(stored, "attempts", 0)
                execution_id = f"{source.mission_id}:{task_id}:attempt:{attempt or 0}"

            role = getattr(task, "role", None)
            common = dict(
                correlation_id=source.mission_id,
                mission_id=source.mission_id,
                task_id=task_id,
                request_id=request_id,
                component="DirectorRuntime",
                role=role,
                provider=provider,
                model=model,
                timestamp=source.created_at,
                metadata={"source_event_id": source.event_id},
            )

            def add(
                event_type: str,
                *,
                suffix: str,
                decision_id: str | None = None,
                status: str | None = None,
                error_code: str | None = None,
                retryable: bool | None = None,
                result_value: Any = None,
                evaluation: Any = None,
                evidence: Any = None,
                started_at: str | None = None,
                completed_at: str | None = None,
                metadata: dict[str, Any] | None = None,
            ) -> None:
                event = ObservabilityEvent(
                    **common,
                    event_id=f"task-event:{source.event_id}:{suffix}",
                    event_type=event_type,
                    decision_id=decision_id,
                    execution_id=execution_id,
                    status=status,
                    error_code=error_code,
                    retryable=retryable,
                    result=result_value,
                    evaluation=evaluation,
                    evidence=evidence,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=_duration_ms(started_at, completed_at),
                    metadata={**common["metadata"], **(metadata or {})},
                )
                observations.append(event)

            if source.event_type == "mission_started":
                add("mission_started", suffix="mission", status="started")
                continue

            if source.event_type == "director_review":
                round_number = payload.get("round")
                decision_id = (
                    decisions_by_round.get(round_number)
                    if isinstance(round_number, int)
                    else f"decision:{source.event_id}"
                )
                add(
                    "decision_created",
                    suffix="decision",
                    decision_id=decision_id,
                    status="proposed",
                    result_value={
                        "decision": payload.get("decision"),
                        "rationale": payload.get("rationale"),
                    },
                    metadata={"round": round_number, "model": payload.get("model")},
                )
                continue

            if source.event_type == "director_decision_executed":
                round_number = payload.get("round")
                decision_id = (
                    decisions_by_round.get(round_number)
                    if isinstance(round_number, int)
                    else latest_decision_id
                )
                add(
                    "decision_executed",
                    suffix="decision-executed",
                    decision_id=decision_id,
                    status="executed",
                    result_value={
                        "decision": payload.get("decision"),
                        "execution": payload.get("execution"),
                        "task_ids": payload.get("task_ids", []),
                    },
                )
                continue

            if source.event_type == "director_decision_evaluated":
                raw_decision_id = payload.get("decision_event_id")
                decision_id = (
                    f"decision:{int(raw_decision_id)}"
                    if raw_decision_id is not None
                    else None
                )
                evidence = {
                    "state": payload.get("evidence_state"),
                    "completeness": payload.get("evidence_completeness"),
                    "outcome": payload.get("outcome"),
                }
                add(
                    "evaluation_completed",
                    suffix="decision-evaluation",
                    decision_id=decision_id,
                    status="evaluated",
                    evaluation=payload,
                    evidence=evidence,
                )
                add(
                    "learning_recorded",
                    suffix="decision-learning",
                    decision_id=decision_id,
                    status="recorded",
                    evaluation=payload,
                    evidence=evidence,
                    metadata={"learning_sink": "DirectorDecisionPerformanceStore"},
                )
                continue

            if source.event_type == "skill.evaluated":
                evidence = {
                    "state": payload.get("evidence_state"),
                    "completeness": payload.get("evidence_completeness"),
                }
                add(
                    "evaluation_completed",
                    suffix="skill-evaluation",
                    status="evaluated",
                    evaluation=payload,
                    evidence=evidence,
                )
                add(
                    "learning_recorded",
                    suffix="skill-learning",
                    status="recorded",
                    evaluation=payload,
                    evidence=evidence,
                    metadata={"learning_sink": "SkillLearningStore"},
                )
                continue

            if source.event_type == "enqueued":
                add(
                    "task_created",
                    suffix="task-created",
                    status="created",
                    result_value={"role": role, "objective": getattr(task, "objective", None)},
                )
                continue

            if source.event_type == "claimed":
                started = source.created_at
                if task_id:
                    task_started_at[task_id] = started
                add("task_started", suffix="task-started", status="running", started_at=started)
                add("execution_started", suffix="execution-started", status="running", started_at=started)
                continue

            if source.event_type == "succeeded":
                completed = source.created_at
                started = task_started_at.get(task_id) if task_id else None
                add(
                    "execution_completed",
                    suffix="execution-completed",
                    status="succeeded",
                    started_at=started,
                    completed_at=completed,
                    result_value=result,
                )
                add(
                    "task_completed",
                    suffix="task-completed",
                    status="succeeded",
                    started_at=started,
                    completed_at=completed,
                    result_value=result,
                )
                continue

            if source.event_type == "retry_scheduled":
                add(
                    "execution_failed",
                    suffix="execution-failed",
                    status="failed",
                    error_code="TASK_EXECUTION_FAILED",
                    retryable=True,
                    result_value={"error": payload.get("error")},
                )
                add(
                    "task_requeued",
                    suffix="task-requeued",
                    status="pending",
                    retryable=True,
                    error_code="TASK_EXECUTION_FAILED",
                    result_value={"error": payload.get("error")},
                )
                continue

            if source.event_type == "requeued":
                add(
                    "execution_recovered",
                    suffix="execution-recovered",
                    status="recovered",
                    retryable=True,
                    metadata={"reason": payload.get("reason")},
                )
                add(
                    "task_requeued",
                    suffix="task-requeued",
                    status="pending",
                    retryable=True,
                    metadata={"reason": payload.get("reason")},
                )
                continue

            if source.event_type in {"succeeded", "failed", "blocked"}:
                add(
                    "task_completed",
                    suffix=f"task-{source.event_type}",
                    status=source.event_type,
                    error_code=payload.get("error_code"),
                    retryable=False,
                )
                continue

            if source.event_type in {"failed", "blocked"}:
                add(
                    "task_completed",
                    suffix=f"task-{source.event_type}",
                    status=source.event_type,
                    error_code=payload.get("error"),
                    retryable=False,
                )
                continue

            if source.event_type in {"mission_succeeded", "mission_failed", "mission_blocked"}:
                round_number = payload.get("round")
                decision_id = (
                    decisions_by_round.get(round_number)
                    if isinstance(round_number, int)
                    else latest_decision_id
                )
                add(
                    "decision_completed",
                    suffix="mission-terminal",
                    decision_id=decision_id,
                    status=source.event_type.removeprefix("mission_"),
                    result_value={
                        "status": source.event_type.removeprefix("mission_"),
                        "reason": payload.get("reason"),
                    },
                )
                continue

        recorded: list[ObservabilityEvent] = []
        for event in observations:
            self.record(event)
            recorded.append(event)
        return tuple(recorded)


__all__ = [
    "ObservabilityEvent",
    "ObservabilitySecurityError",
    "ProductionObservability",
]
