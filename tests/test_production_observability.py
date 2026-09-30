"""Contract tests for PRODUCTION-OBSERVABILITY-V1.

These tests define the production observability boundary before implementation.
The implementation must make every test pass without changing decision semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aurora.observability import (
    ObservabilityEvent,
    ObservabilitySecurityError,
    ProductionObservability,
)


def _event(**overrides):
    values = {
        "event_type": "execution_completed",
        "correlation_id": "corr-1",
        "mission_id": "mission-1",
        "decision_id": "decision-1",
        "task_id": "task-1",
        "execution_id": "exec-1",
        "request_id": "req-1",
        "component": "ModelRouter",
        "agent_id": "agent-1",
        "provider": "nvidia_api",
        "model": "test-model",
        "status": "success",
        "started_at": "2026-09-30T18:00:00+00:00",
        "completed_at": "2026-09-30T18:00:01+00:00",
        "duration_ms": 1000,
        "error_code": None,
        "retryable": False,
        "result": {"kind": "text", "size": 12},
        "evaluation": {"state": "evidence_complete", "score": 1.0},
        "evidence": {"completeness": 1.0},
        "metadata": {"safe": "value"},
    }
    values.update(overrides)
    return ObservabilityEvent(**values)


def test_event_has_canonical_identity_and_execution_fields():
    event = _event()
    payload = event.to_dict()

    for key in (
        "event_id",
        "event_type",
        "schema_version",
        "timestamp",
        "correlation_id",
        "mission_id",
        "decision_id",
        "task_id",
        "execution_id",
        "request_id",
    ):
        assert key in payload

    assert payload["correlation_id"] == "corr-1"
    assert payload["execution_id"] == "exec-1"


def test_event_requires_orderable_identity():
    first = _event()
    second = _event()

    assert first.event_id != second.event_id
    assert first.timestamp <= second.timestamp


def test_failed_execution_preserves_typed_error_and_retryability():
    event = _event(
        event_type="execution_failed",
        status="failed",
        error_code="PROVIDER_TIMEOUT",
        retryable=True,
    )

    payload = event.to_dict()

    assert payload["status"] == "failed"
    assert payload["error_code"] == "PROVIDER_TIMEOUT"
    assert payload["retryable"] is True


def test_observability_persists_and_reloads_after_restart(tmp_path: Path):
    path = tmp_path / "observability.jsonl"

    first = ProductionObservability(path)
    first.record(_event(event_type="execution_started"))
    first.record(_event(event_type="execution_completed"))
    del first

    second = ProductionObservability(path)
    events = second.reconstruct_cycle("corr-1")

    assert [event.event_type for event in events] == [
        "execution_started",
        "execution_completed",
    ]
    assert all(event.correlation_id == "corr-1" for event in events)


def test_cycle_reconstruction_preserves_end_to_end_order(tmp_path: Path):
    path = tmp_path / "observability.jsonl"
    observer = ProductionObservability(path)

    event_types = [
        "decision_created",
        "task_created",
        "execution_started",
        "execution_completed",
        "evaluation_completed",
        "learning_recorded",
        "decision_completed",
    ]

    for event_type in event_types:
        observer.record(_event(event_type=event_type))

    reconstructed = observer.reconstruct_cycle("corr-1")
    assert [event.event_type for event in reconstructed] == event_types


def test_observability_rejects_secrets(tmp_path: Path):
    observer = ProductionObservability(tmp_path / "observability.jsonl")

    with pytest.raises(ObservabilitySecurityError):
        observer.record(
            _event(
                metadata={
                    "Authorization": "Bearer super-secret-token",
                    "NVIDIA_API_KEY": "secret",
                }
            )
        )


def test_observability_never_serializes_credentials(tmp_path: Path):
    path = tmp_path / "observability.jsonl"
    observer = ProductionObservability(path)

    observer.record(_event(metadata={"safe": "value"}))

    raw = path.read_text(encoding="utf-8")
    assert "Authorization" not in raw
    assert "Bearer " not in raw
    assert "API_KEY" not in raw
    json.loads(raw.splitlines()[0])


def test_model_and_provider_are_actual_execution_metadata(tmp_path: Path):
    observer = ProductionObservability(tmp_path / "observability.jsonl")

    observer.record(
        _event(
            provider="gemini",
            model="gemini-test-model",
            event_type="execution_completed",
        )
    )

    event = observer.reconstruct_cycle("corr-1")[0]
    assert event.provider == "gemini"
    assert event.model == "gemini-test-model"


def test_learning_event_keeps_evaluation_and_evidence_linked(tmp_path: Path):
    observer = ProductionObservability(tmp_path / "observability.jsonl")

    observer.record(
        _event(
            event_type="learning_recorded",
            evaluation={"state": "evidence_complete", "score": 1.0},
            evidence={"completeness": 1.0, "source": "skill-evaluator"},
        )
    )

    event = observer.reconstruct_cycle("corr-1")[0]
    assert event.evaluation["state"] == "evidence_complete"
    assert event.evidence["completeness"] == 1.0


def test_observability_is_not_authoritative_for_decision_result(tmp_path: Path):
    observer = ProductionObservability(tmp_path / "observability.jsonl")

    decision_result = {"action": "continue", "score": 0.73}
    recorded_result = observer.observe_decision(
        correlation_id="corr-1",
        decision_id="decision-1",
        result=decision_result,
    )

    assert recorded_result == decision_result
    assert recorded_result is decision_result
