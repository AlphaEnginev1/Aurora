from pathlib import Path

import pytest

from aurora.orchestration.task_engine import DelegatedTask
from aurora.orchestration.task_store import (
    DurableTaskStore,
    TaskStoreError,
)


def plan():
    return (
        DelegatedTask("collect", "researcher", "collect evidence"),
        DelegatedTask(
            "review",
            "reviewer",
            "review evidence",
            depends_on=("collect",),
        ),
    )


def test_task_store_persists_plan_and_events(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-1", plan())

    tasks = store.list_tasks("mission-1")
    assert [item.task.task_id for item in tasks] == ["collect", "review"]
    assert all(item.status == "pending" for item in tasks)
    assert [event.event_type for event in store.events("mission-1")] == [
        "enqueued",
        "enqueued",
    ]


def test_claim_is_dependency_aware_and_atomic(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-1", plan())

    first = store.claim_next("mission-1", "worker-a")
    assert first is not None
    assert first.task.task_id == "collect"
    assert first.status == "running"
    assert first.attempts == 1

    assert store.claim_next("mission-1", "worker-b") is None

    store.complete("mission-1", "collect", status="succeeded", payload={"returncode": 0})

    second = store.claim_next("mission-1", "worker-b")
    assert second is not None
    assert second.task.task_id == "review"
    assert second.attempts == 1


def test_failed_dependency_blocks_dependents(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-1", plan())

    first = store.claim_next("mission-1", "worker-a")
    assert first is not None
    store.complete("mission-1", "collect", status="failed", error="source unavailable")

    changed = store.block_dependents("mission-1")
    assert changed == 1

    review = store.get("mission-1", "review")
    assert review is not None
    assert review.status == "blocked"
    assert review.last_error == "dependency did not succeed"


def test_store_survives_reopen(tmp_path: Path):
    path = tmp_path / "tasks.db"
    DurableTaskStore(path).enqueue("mission-1", plan())

    reopened = DurableTaskStore(path)
    claimed = reopened.claim_next("mission-1", "worker-restarted")
    assert claimed is not None
    assert claimed.task.task_id == "collect"


def test_duplicate_enqueue_is_rejected_without_partial_commit(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-1", plan())

    with pytest.raises(TaskStoreError, match="already exists"):
        store.enqueue(
            "mission-1",
            (
                DelegatedTask("collect", "researcher", "duplicate"),
                DelegatedTask("new", "agent", "new task"),
            ),
        )

    assert [item.task.task_id for item in store.list_tasks("mission-1")] == [
        "collect",
        "review",
    ]


def test_missing_dependency_is_rejected(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    with pytest.raises(TaskStoreError, match="unknown dependency"):
        store.enqueue(
            "mission-1",
            [DelegatedTask("a", "agent", "work", depends_on=("missing",))],
        )
