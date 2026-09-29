from datetime import datetime, timedelta, timezone
from pathlib import Path

from aurora.inference.models import InferenceResponse
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import DelegatedTask, DelegatedTaskResult, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore, TaskStoreError
from aurora.orchestration.task_worker import TaskWorker, WorkerPolicy


class FakeOrchestration:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("transient failure")
        return object()

    def run_capability_task(self, **kwargs):
        raise AssertionError("capability path is not expected in this test")


def make_worker(tmp_path: Path, *, failures: int = 0, max_attempts: int = 3):
    orchestration = FakeOrchestration(failures=failures)
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    store = DurableTaskStore(tmp_path / "tasks.db")
    worker = TaskWorker(
        store=store,
        task_engine=engine,
        mission_id="mission-1",
        worker_id="worker-a",
        policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=max_attempts,
            idle_sleep_seconds=0,
        ),
    )
    return store, worker, orchestration


def test_worker_executes_dependency_chain_durably(tmp_path: Path):
    store, worker, orchestration = make_worker(tmp_path)
    store.enqueue(
        "mission-1",
        (
            DelegatedTask("a", "researcher", "collect"),
            DelegatedTask("b", "reviewer", "review", depends_on=("a",)),
        ),
    )

    cycles = worker.run_until_idle(max_cycles=4)

    assert [cycle.action for cycle in cycles] == ["succeeded", "succeeded", "idle"]
    assert [item.status for item in store.list_tasks("mission-1")] == [
        "succeeded",
        "succeeded",
    ]
    assert [call["task_id"] for call in orchestration.calls] == ["a", "b"]


def test_worker_retries_transient_failure_without_losing_task(tmp_path: Path):
    store, worker, orchestration = make_worker(
        tmp_path,
        failures=1,
        max_attempts=2,
    )
    store.enqueue(
        "mission-1",
        [DelegatedTask("a", "agent", "work")],
    )

    first = worker.run_once()
    second = worker.run_once()

    assert first.action == "retried"
    assert second.action == "succeeded"
    task = store.get("mission-1", "a")
    assert task is not None
    assert task.status == "succeeded"
    assert task.attempts == 2
    assert len(orchestration.calls) == 2
    assert [event.event_type for event in store.events("mission-1")] == [
        "enqueued",
        "claimed",
        "retry_scheduled",
        "claimed",
        "succeeded",
    ]


def test_worker_fails_after_max_attempts_and_blocks_dependents(tmp_path: Path):
    store, worker, _ = make_worker(tmp_path, failures=5, max_attempts=1)
    store.enqueue(
        "mission-1",
        (
            DelegatedTask("a", "agent", "fail"),
            DelegatedTask("b", "agent", "dependent", depends_on=("a",)),
        ),
    )

    cycle = worker.run_once()

    assert cycle.action == "failed"
    assert cycle.blocked_tasks == 1
    assert store.get("mission-1", "a").status == "failed"
    assert store.get("mission-1", "b").status == "blocked"


def test_expired_worker_lease_is_requeued_after_restart(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-1", [DelegatedTask("a", "agent", "work")])

    claimed = store.claim_next("mission-1", "worker-crashed", lease_seconds=30)
    assert claimed is not None
    assert claimed.claimed_by == "worker-crashed"

    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    assert store.recover_expired("mission-1", now=future.isoformat()) == 1

    recovered = store.claim_next("mission-1", "worker-restarted", lease_seconds=30)
    assert recovered is not None
    assert recovered.task.task_id == "a"
    assert recovered.attempts == 2
    assert recovered.claimed_by == "worker-restarted"

    events = store.events("mission-1")
    assert [event.event_type for event in events] == [
        "enqueued",
        "claimed",
        "requeued",
        "claimed",
    ]


def test_lease_renewal_requires_owner(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-1", [DelegatedTask("a", "agent", "work")])
    claimed = store.claim_next("mission-1", "worker-a", lease_seconds=30)
    assert claimed is not None

    try:
        store.renew_lease("mission-1", "a", "worker-b", lease_seconds=30)
    except TaskStoreError as exc:
        assert "does not own" in str(exc)
    else:
        raise AssertionError("wrong worker unexpectedly renewed the lease")

    renewed = store.renew_lease("mission-1", "a", "worker-a", lease_seconds=30)
    assert renewed > claimed.lease_until


def test_run_until_idle_rejects_nonconvergent_worker(tmp_path: Path):
    store, worker, _ = make_worker(tmp_path, failures=0)
    store.enqueue("mission-1", [DelegatedTask("a", "agent", "work")])

    # One cycle is not enough: the worker must execute then observe idle.
    try:
        worker.run_until_idle(max_cycles=1)
    except Exception as exc:
        assert "max_cycles" in str(exc)
    else:
        raise AssertionError("worker unexpectedly reported convergence")

def test_worker_persists_routing_evidence_for_director_review(tmp_path: Path):
    response = InferenceResponse(
        model="model-a",
        provider="nvidia",
        content="completed",
        selected_model="model-a",
        routing_score=0.83,
        routing_factors={"learned_score": 0.9, "quality": 0.8},
        routing_candidates=(("model-a", 0.83), ("model-b", 0.71)),
    )
    result = DelegatedTaskResult(
        task=DelegatedTask("a", "agent", "work"),
        status=TaskStatus.SUCCEEDED,
        model_result=TaskResult(task_id="a", role="agent", response=response),
    )

    payload = TaskWorker._result_payload(result)

    assert payload["routing"] == {
        "selected_model": "model-a",
        "score": 0.83,
        "factors": {"learned_score": 0.9, "quality": 0.8},
        "candidates": [
            {"model": "model-a", "score": 0.83},
            {"model": "model-b", "score": 0.71},
        ],
    }
