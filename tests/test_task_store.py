from datetime import datetime, timedelta, timezone
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


def test_task_store_persists_result_and_phase(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue(
        "mission-1",
        [DelegatedTask("a", "agent", "work")],
        phase=2,
    )
    claimed = store.claim_next("mission-1", "worker-a")
    assert claimed is not None
    store.complete(
        "mission-1",
        "a",
        status="succeeded",
        result={"type": "model", "content": "useful evidence"},
        worker_id="worker-a",
    )

    reopened = DurableTaskStore(tmp_path / "tasks.db")
    item = reopened.get("mission-1", "a")
    assert item is not None
    assert item.phase == 2
    assert item.result == {"type": "model", "content": "useful evidence"}


def test_later_phase_supersedes_previous_failed_phase(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue(
        "mission-1",
        [DelegatedTask("a", "agent", "initial")],
        phase=0,
    )
    claimed = store.claim_next("mission-1", "worker-a")
    assert claimed is not None
    store.complete("mission-1", "a", status="failed", error="temporary", worker_id="worker-a")
    assert store.mission_status("mission-1") == "failed"

    store.enqueue(
        "mission-1",
        [DelegatedTask("b", "agent", "recovery")],
        phase=1,
    )
    assert store.mission_status("mission-1") == "pending"
    recovered = store.claim_next("mission-1", "worker-b")
    assert recovered is not None
    assert recovered.task.task_id == "b"
    store.complete("mission-1", "b", status="succeeded", worker_id="worker-b")
    assert store.mission_status("mission-1") == "succeeded"


def test_later_phase_can_depend_on_succeeded_prior_phase_task(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "cross-phase-success.db")
    store.enqueue(
        "mission-cross-phase",
        [DelegatedTask("collect", "researcher", "collect evidence")],
        phase=0,
    )
    first = store.claim_next("mission-cross-phase", "worker-a")
    assert first is not None
    store.complete(
        "mission-cross-phase",
        "collect",
        status="succeeded",
        worker_id="worker-a",
        claim_token=first.claim_token,
        result={"type": "model", "content": "evidence"},
    )

    store.enqueue(
        "mission-cross-phase",
        [
            DelegatedTask(
                "r1_validate",
                "validator",
                "validate prior evidence",
                depends_on=("collect",),
            )
        ],
        phase=1,
    )

    second = store.claim_next("mission-cross-phase", "worker-b")
    assert second is not None
    assert second.task.task_id == "r1_validate"
    assert second.task.depends_on == ("collect",)


def test_later_phase_rejects_succeeded_prior_task_without_result_receipt(
    tmp_path: Path,
):
    store = DurableTaskStore(tmp_path / "cross-phase-missing-result.db")
    store.enqueue(
        "mission-cross-phase-missing-result",
        [DelegatedTask("collect", "researcher", "collect evidence")],
        phase=0,
    )
    first = store.claim_next("mission-cross-phase-missing-result", "worker-a")
    assert first is not None
    store.complete(
        "mission-cross-phase-missing-result",
        "collect",
        status="succeeded",
        worker_id="worker-a",
        claim_token=first.claim_token,
    )

    with pytest.raises(TaskStoreError, match="no durable result receipt"):
        store.enqueue(
            "mission-cross-phase-missing-result",
            [
                DelegatedTask(
                    "r1_validate",
                    "validator",
                    "validate prior evidence",
                    depends_on=("collect",),
                )
            ],
            phase=1,
        )

    assert [item.task.task_id for item in store.list_tasks(
        "mission-cross-phase-missing-result"
    )] == ["collect"]


def test_later_phase_rejects_non_succeeded_prior_dependency(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "cross-phase-failed.db")
    store.enqueue(
        "mission-cross-phase-failed",
        [DelegatedTask("collect", "researcher", "collect evidence")],
        phase=0,
    )
    first = store.claim_next("mission-cross-phase-failed", "worker-a")
    assert first is not None
    store.complete(
        "mission-cross-phase-failed",
        "collect",
        status="failed",
        worker_id="worker-a",
        claim_token=first.claim_token,
        error="source unavailable",
    )

    with pytest.raises(TaskStoreError, match="must have succeeded"):
        store.enqueue(
            "mission-cross-phase-failed",
            [
                DelegatedTask(
                    "r1_validate",
                    "validator",
                    "validate prior evidence",
                    depends_on=("collect",),
                )
            ],
            phase=1,
        )

    assert [item.task.task_id for item in store.list_tasks("mission-cross-phase-failed")] == [
        "collect"
    ]


def test_task_lifecycle_events_inherit_project_role_and_correlation_context(
    tmp_path: Path,
):
    store = DurableTaskStore(tmp_path / "event-context.db")
    mission_id = "mission-event-context"
    project_id = "project-a"
    correlation_id = "corr-event-context"

    store.enqueue(
        mission_id,
        [
            DelegatedTask(
                "project-task",
                "researcher",
                "inspect project evidence",
                project_id=project_id,
            )
        ],
        correlation_id=correlation_id,
        initial_events=(
            {
                "event_type": "mission_submitted",
                "payload": {
                    "objective": "inspect project evidence",
                    "project_id": project_id,
                    "correlation_id": correlation_id,
                },
                "project_id": project_id,
                "correlation_id": correlation_id,
            },
        ),
    )

    claimed = store.claim_next(mission_id, "worker-a", lease_seconds=30)
    assert claimed is not None
    store.renew_lease(
        mission_id,
        "project-task",
        "worker-a",
        claim_token=claimed.claim_token,
        lease_seconds=30,
    )
    store.complete(
        mission_id,
        "project-task",
        status="succeeded",
        result={"type": "model", "content": "verified"},
        worker_id="worker-a",
        claim_token=claimed.claim_token,
    )

    lifecycle = [
        event for event in store.events(mission_id)
        if event.event_type in {"enqueued", "claimed", "lease_renewed", "succeeded"}
    ]
    assert {event.event_type for event in lifecycle} == {
        "enqueued", "claimed", "lease_renewed", "succeeded"
    }
    assert all(event.project_id == project_id for event in lifecycle)
    assert all(event.agent_role == "researcher" for event in lifecycle)
    assert all(event.correlation_id == correlation_id for event in lifecycle)
    scoped = store.latest_events(limit=50, project_id=project_id)
    assert {event.event_type for event in scoped} >= {
        "enqueued", "claimed", "lease_renewed", "succeeded"
    }


def test_enqueue_rolls_back_task_rows_and_initial_events_as_one_transaction(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    tasks = (
        DelegatedTask("valid-a", "researcher", "valid task A"),
        DelegatedTask("valid-b", "reviewer", "valid task B"),
    )

    with pytest.raises(TypeError):
        store.enqueue(
            "mission-atomic",
            tasks,
            initial_events=(
                {
                    "event_type": "mission_submitted",
                    "payload": {"objective": "atomic test", "task_ids": ["valid-a", "valid-b"]},
                },
                {
                    "event_type": "team.formed",
                    # The non-serializable value fails only after both tasks,
                    # their enqueued events and the first receipt were staged.
                    "payload": {"roles": [object()]},
                },
            ),
        )

    assert store.list_tasks("mission-atomic") == ()
    assert store.events("mission-atomic") == ()
