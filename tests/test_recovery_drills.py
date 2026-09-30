from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.event_bus import DurableEventBus, EventSubscription
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore, TaskStoreError
from aurora.orchestration.task_worker import TaskWorker, WorkerPolicy
from aurora.observability import ProductionObservability


class _RecoveryOrchestration:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs["task_id"])
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="recovery/model",
                provider="recovery-test",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
                requested_model=kwargs.get("model"),
                selected_model="recovery/model",
                reported_model="recovery/model",
                routing_score=0.8,
                routing_factors={"quality": 0.8},
                routing_candidates=(("recovery/model", 0.8),),
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("capability recovery drill does not use capabilities")


class _RecoveryPlanner:
    def plan(self, **kwargs):
        return DirectorPlan(
            objective=kwargs["objective"],
            tasks=(
                DelegatedTask("collect", "agent", "collect evidence"),
                DelegatedTask(
                    "review",
                    "agent",
                    "review evidence",
                    depends_on=("collect",),
                ),
            ),
            model="director/model",
        )

    def review(self, **kwargs):
        return DirectorReview(
            decision="complete",
            rationale="Recovery evidence is sufficient.",
            tasks=(),
            model="director/model",
        )


def _make_worker(tmp_path: Path, *, worker_id: str, engine: TaskEngine, store: DurableTaskStore):
    return TaskWorker(
        store=store,
        task_engine=engine,
        mission_id="mission-recovery",
        worker_id=worker_id,
        policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=2,
            idle_sleep_seconds=0,
        ),
    )


def test_rd01_worker_crash_after_claim_is_recovered_and_executed_once(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    orchestration = _RecoveryOrchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    store.enqueue("mission-recovery", [DelegatedTask("a", "agent", "work")])

    crashed = store.claim_next("mission-recovery", "crashed-worker", lease_seconds=1)
    assert crashed is not None

    expired = datetime.now(timezone.utc) + timedelta(seconds=2)
    assert store.recover_expired("mission-recovery", now=expired.isoformat()) == 1

    restarted = _make_worker(
        tmp_path,
        worker_id="restarted-worker",
        engine=engine,
        store=store,
    )
    cycle = restarted.run_once()

    assert cycle.action == "succeeded"
    assert orchestration.calls == ["a"]
    recovered = store.get("mission-recovery", "a")
    assert recovered is not None
    assert recovered.status == "succeeded"
    assert recovered.attempts == 2

    assert [event.event_type for event in store.events("mission-recovery")] == [
        "enqueued",
        "claimed",
        "requeued",
        "claimed",
        "succeeded",
    ]


def test_rd02_stale_worker_cannot_commit_after_recovery(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-recovery", [DelegatedTask("a", "agent", "work")])

    crashed = store.claim_next("mission-recovery", "crashed-worker", lease_seconds=1)
    assert crashed is not None

    expired = datetime.now(timezone.utc) + timedelta(seconds=2)
    assert store.recover_expired("mission-recovery", now=expired.isoformat()) == 1
    replacement = store.claim_next("mission-recovery", "restarted-worker", lease_seconds=30)
    assert replacement is not None
    assert replacement.claim_token != crashed.claim_token

    with pytest.raises(TaskStoreError, match="does not own"):
        store.complete(
            "mission-recovery",
            "a",
            status="succeeded",
            result={"actor": "stale-worker"},
            worker_id="crashed-worker",
            claim_token=crashed.claim_token,
        )

    current = store.get("mission-recovery", "a")
    assert current is not None
    assert current.status == "running"
    assert current.claimed_by == "restarted-worker"


def test_rd03_director_restart_reconciles_dependency_chain_without_duplicate_execution(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    orchestration = _RecoveryOrchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    director = DurableDirectorService(
        planner=_RecoveryPlanner(),
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    observation = ProductionObservability(tmp_path / "observability.jsonl")
    first_runtime = DirectorRuntime(
        director=director,
        store=store,
        event_bus=bus,
        observability=observation,
        worker_policy=WorkerPolicy(lease_seconds=1, max_attempts=1, idle_sleep_seconds=0),
        worker_id="director-before-crash",
        event_consumer_id="director-before-crash-events",
        worker_concurrency=1,
    )

    submission = first_runtime.submit_mission(objective="recover the mission")
    claimed = store.claim_next(
        submission.mission_id,
        "crashed-worker",
        lease_seconds=1,
    )
    assert claimed is not None
    assert store.mission_status(submission.mission_id) == "running"

    restart_runtime = DirectorRuntime(
        director=director,
        store=store,
        event_bus=bus,
        observability=ProductionObservability(tmp_path / "observability.jsonl"),
        worker_policy=WorkerPolicy(lease_seconds=30, max_attempts=1, idle_sleep_seconds=0),
        worker_id="director-after-restart",
        event_consumer_id="director-after-restart-events",
        worker_concurrency=1,
    )

    # Use an explicit future so the drill does not depend on wall-clock sleep.
    recovered = store.recover_expired(
        submission.mission_id,
        now=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
    )
    assert recovered == 1

    result = restart_runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert orchestration.calls == ["collect", "review"]
    events = store.events(submission.mission_id)
    assert sum(event.event_type == "mission_succeeded" for event in events) == 1
    assert any(event.event_type == "requeued" for event in events)

    reconstructed = ProductionObservability(
        tmp_path / "observability.jsonl"
    ).reconstruct_cycle(submission.mission_id)
    assert reconstructed
    execution_ids = [
        event.execution_id
        for event in reconstructed
        if event.event_type == "execution_completed"
    ]
    assert len(execution_ids) == 2
    assert len(set(execution_ids)) == 2


def test_rd04_recovered_predecessor_allows_dependent_task_to_run(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    orchestration = _RecoveryOrchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    worker = _make_worker(
        tmp_path,
        worker_id="recovery-worker",
        engine=engine,
        store=store,
    )
    store.enqueue(
        "mission-recovery",
        (
            DelegatedTask("a", "agent", "recover predecessor"),
            DelegatedTask("b", "agent", "run dependent", depends_on=("a",)),
        ),
    )

    crashed = store.claim_next("mission-recovery", "crashed-worker", lease_seconds=1)
    assert crashed is not None
    assert store.recover_expired(
        "mission-recovery",
        now=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
    ) == 1

    first = worker.run_once()
    second = worker.run_once()

    assert first.action == "succeeded"
    assert second.action == "succeeded"
    assert orchestration.calls == ["a", "b"]


def test_rd05_unacked_event_survives_consumer_crash_and_replays_at_least_once(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "events.db")
    bus = DurableEventBus(store)

    producer = bus.subscribe("recovery-consumer")
    first = bus.publish(
        mission_id="mission-recovery",
        event_type="mission_started",
        payload={"worker_id": "worker-a"},
    )
    second = bus.publish(
        mission_id="mission-recovery",
        event_type="task_started",
        task_id="a",
        payload={"attempt": 1},
    )

    delivered_before_crash = bus.poll(producer)
    assert [event.event_id for event in delivered_before_crash] == [
        first.event_id,
        second.event_id,
    ]

    bus.ack(producer, first.event_id)

    restarted_bus = DurableEventBus(DurableTaskStore(tmp_path / "events.db"))
    restarted = restarted_bus.subscribe("recovery-consumer")
    replayed = restarted_bus.poll(restarted)

    assert [event.event_id for event in replayed] == [second.event_id]
    restarted_bus.ack(restarted, second.event_id)
    assert restarted_bus.poll(restarted) == ()
