from pathlib import Path
import sqlite3

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


def test_store_migrates_existing_prelease_database(tmp_path: Path):
    path = tmp_path / "tasks.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE tasks (
                mission_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                role TEXT NOT NULL,
                objective TEXT NOT NULL,
                capability TEXT,
                args_json TEXT NOT NULL,
                deps_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','blocked')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (mission_id, task_id)
            );
            CREATE TABLE task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                task_id TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    store = DurableTaskStore(path)
    store.enqueue("mission-1", [DelegatedTask("a", "agent", "work")])
    claimed = store.claim_next("mission-1", "worker-a", lease_seconds=30)

    assert claimed is not None
    assert claimed.lease_until is not None
    assert claimed.claimed_by == "worker-a"


def test_complete_rejects_an_expired_owned_lease(tmp_path: Path):
    path = tmp_path / "tasks.db"
    store = DurableTaskStore(path)
    store.enqueue("mission-1", [DelegatedTask("a", "agent", "work")])
    claimed = store.claim_next("mission-1", "worker-a", lease_seconds=30)
    assert claimed is not None

    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    # Recovering the lease models a different worker taking ownership after
    # the original worker stopped heartbeating.
    assert store.recover_expired("mission-1", now=future.isoformat()) == 1
    replacement = store.claim_next("mission-1", "worker-b", lease_seconds=30)
    assert replacement is not None

    with pytest.raises(TaskStoreError, match="does not own"):
        store.complete(
            "mission-1",
            "a",
            status="succeeded",
            worker_id="worker-a",
        )
