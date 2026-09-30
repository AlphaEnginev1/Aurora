"""Contract tests for PRODUCTION-OBSERVABILITY-V1.

These tests define the production observability boundary before implementation.
The implementation must make every test pass without changing decision semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy
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


class _IntegrationPlanner:
    def plan(self, **kwargs):
        return DirectorPlan(
            objective=kwargs["objective"],
            tasks=(DelegatedTask("collect", "agent", "collect evidence"),),
            model="director/model",
        )

    def review(self, **kwargs):
        return DirectorReview(
            decision="complete",
            rationale="Observed execution is sufficient.",
            tasks=(),
            model="director/model",
        )


class _IntegrationOrchestration:
    def run_task(self, **kwargs):
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="actual/model",
                provider="nvidia_api",
                content="observed result",
                request_id="request-real-1",
                execution_id="execution-real-1",
                selected_model="actual/model",
                reported_model="actual/model",
                routing_score=0.91,
                routing_factors={"quality": 0.9},
                routing_candidates=(("actual/model", 0.91),),
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("capability execution is not expected")


def test_real_director_cycle_is_reconstructable_after_runtime_restart(tmp_path: Path):
    db = tmp_path / "tasks.db"
    observation_path = tmp_path / "observability.jsonl"
    store = DurableTaskStore(db)
    bus = DurableEventBus(store)
    engine = TaskEngine(_IntegrationOrchestration(), tmp_path / "workspace")
    director = DurableDirectorService(
        planner=_IntegrationPlanner(),
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    observer = ProductionObservability(observation_path)
    runtime = DirectorRuntime(
        director=director,
        store=store,
        event_bus=bus,
        observability=observer,
        worker_policy=WorkerPolicy(lease_seconds=30, max_attempts=1, idle_sleep_seconds=0),
        worker_id="integration-worker",
        event_consumer_id="integration-events",
        worker_concurrency=1,
    )

    submission = runtime.submit_mission(objective="prove observability")
    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    events = observer.reconstruct_cycle(submission.mission_id)
    event_types = [event.event_type for event in events]
    for name in (
        "mission_started",
        "task_created",
        "task_started",
        "execution_started",
        "execution_completed",
        "task_completed",
        "decision_created",
        "decision_executed",
        "evaluation_completed",
        "learning_recorded",
        "decision_completed",
    ):
        assert name in event_types

    positions = {name: event_types.index(name) for name in event_types}
    assert positions["task_created"] < positions["task_started"]
    assert positions["task_started"] <= positions["execution_started"]
    assert positions["execution_started"] < positions["execution_completed"]
    assert positions["execution_completed"] <= positions["task_completed"]
    assert positions["task_completed"] < positions["decision_created"]
    assert positions["decision_created"] <= positions["decision_executed"]
    assert positions["decision_executed"] < positions["evaluation_completed"]
    assert positions["evaluation_completed"] < positions["learning_recorded"]
    assert positions["learning_recorded"] < positions["decision_completed"]

    execution = next(event for event in events if event.event_type == "execution_completed")
    assert execution.correlation_id == submission.mission_id
    assert execution.task_id == "collect"
    assert execution.execution_id == "execution-real-1"
    assert execution.request_id == "request-real-1"
    assert execution.provider == "nvidia_api"
    assert execution.model == "actual/model"
    assert execution.result["type"] == "model"
    assert execution.duration_ms is not None

    decisions = {
        event.event_type: event
        for event in events
        if event.event_type in {"decision_created", "decision_executed", "evaluation_completed", "decision_completed"}
    }
    assert decisions["decision_created"].decision_id
    assert decisions["decision_executed"].decision_id == decisions["decision_created"].decision_id
    assert decisions["evaluation_completed"].decision_id == decisions["decision_created"].decision_id
    assert decisions["decision_completed"].decision_id == decisions["decision_created"].decision_id

    before = len(observation_path.read_text(encoding="utf-8").splitlines())
    observer_after_restart = ProductionObservability(observation_path)
    runtime_after_restart = DirectorRuntime(
        director=director,
        store=store,
        event_bus=bus,
        observability=observer_after_restart,
        worker_policy=WorkerPolicy(lease_seconds=30, max_attempts=1, idle_sleep_seconds=0),
        worker_id="integration-worker-restarted",
        event_consumer_id="integration-events-restarted",
        worker_concurrency=1,
    )
    runtime_after_restart.run_once()
    after = len(observation_path.read_text(encoding="utf-8").splitlines())
    assert after == before
    assert [event.event_id for event in observer_after_restart.reconstruct_cycle(submission.mission_id)] == [
        event.event_id for event in events
    ]
