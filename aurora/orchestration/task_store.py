"""Durable task queue and event journal for long-running Aurora missions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Sequence

from .task_engine import DelegatedTask


class TaskStoreError(RuntimeError):
    """Raised when durable task state cannot be advanced safely."""


@dataclass(frozen=True)
class StoredTask:
    mission_id: str
    task: DelegatedTask
    status: str
    attempts: int
    last_error: str | None = None
    claimed_by: str | None = None
    lease_until: str | None = None


@dataclass(frozen=True)
class TaskEvent:
    event_id: int
    mission_id: str
    task_id: str | None
    event_type: str
    payload: dict
    created_at: str


class DurableTaskStore:
    """SQLite-backed queue with atomic claims, leases, and append-only events."""

    VALID_STATUSES = frozenset({"pending", "running", "succeeded", "failed", "blocked"})

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
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
                    claimed_by TEXT,
                    lease_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (mission_id, task_id)
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    task_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_runnable
                    ON tasks (mission_id, status, created_at);

                CREATE INDEX IF NOT EXISTS idx_tasks_lease
                    ON tasks (status, lease_until);

                CREATE INDEX IF NOT EXISTS idx_events_mission
                    ON task_events (mission_id, event_id);
                """
            )
            columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "claimed_by" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN claimed_by TEXT")
            if "lease_until" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN lease_until TEXT")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _lease_until(seconds: float) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    @staticmethod
    def _validate_lease_seconds(lease_seconds: float) -> None:
        if lease_seconds <= 0:
            raise TaskStoreError("lease_seconds must be positive")

    def enqueue(self, mission_id: str, tasks: Sequence[DelegatedTask]) -> None:
        if not mission_id.strip():
            raise TaskStoreError("mission_id is empty")
        tasks = tuple(tasks)
        if not tasks:
            raise TaskStoreError("cannot enqueue an empty task plan")
        ids = [task.task_id for task in tasks]
        if len(set(ids)) != len(ids):
            raise TaskStoreError("task ids must be unique")
        known = set(ids)
        if any(dep not in known for task in tasks for dep in task.depends_on):
            raise TaskStoreError("task plan contains an unknown dependency")

        now = self._now()
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                for task in tasks:
                    db.execute(
                        """
                        INSERT INTO tasks (
                            mission_id, task_id, role, objective, capability,
                            args_json, deps_json, status, attempts,
                            last_error, claimed_by, lease_until, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, NULL, NULL, ?, ?)
                        """,
                        (
                            mission_id,
                            task.task_id,
                            task.role,
                            task.objective,
                            task.capability,
                            json.dumps(list(task.args)),
                            json.dumps(list(task.depends_on)),
                            now,
                            now,
                        ),
                    )
                    self._event_locked(
                        db,
                        mission_id,
                        task.task_id,
                        "enqueued",
                        {"role": task.role, "capability": task.capability},
                    )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise TaskStoreError(
                    f"task already exists in mission or plan is invalid: {mission_id}"
                ) from exc
            except Exception:
                db.rollback()
                raise

    def claim_next(
        self,
        mission_id: str,
        worker_id: str,
        *,
        lease_seconds: float = 300.0,
    ) -> StoredTask | None:
        if not worker_id.strip():
            raise TaskStoreError("worker_id is empty")
        self._validate_lease_seconds(lease_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT * FROM tasks
                WHERE mission_id = ? AND status = 'pending'
                ORDER BY created_at, task_id
                """,
                (mission_id,),
            ).fetchall()

            for row in rows:
                dependencies = tuple(json.loads(row["deps_json"]))
                if any(
                    not self._dependency_succeeded(db, mission_id, dep)
                    for dep in dependencies
                ):
                    continue

                now = self._now()
                lease_until = self._lease_until(lease_seconds)
                updated = db.execute(
                    """
                    UPDATE tasks
                    SET status = 'running',
                        attempts = attempts + 1,
                        claimed_by = ?,
                        lease_until = ?,
                        updated_at = ?
                    WHERE mission_id = ? AND task_id = ? AND status = 'pending'
                    """,
                    (
                        worker_id,
                        lease_until,
                        now,
                        mission_id,
                        row["task_id"],
                    ),
                )
                if updated.rowcount != 1:
                    continue
                self._event_locked(
                    db,
                    mission_id,
                    row["task_id"],
                    "claimed",
                    {
                        "worker_id": worker_id,
                        "attempt": row["attempts"] + 1,
                        "lease_until": lease_until,
                    },
                )
                db.commit()
                return self._row_to_task(
                    row,
                    status="running",
                    attempts=row["attempts"] + 1,
                    last_error=None,
                    claimed_by=worker_id,
                    lease_until=lease_until,
                )

            db.commit()
            return None

    def renew_lease(
        self,
        mission_id: str,
        task_id: str,
        worker_id: str,
        *,
        lease_seconds: float = 300.0,
    ) -> str:
        if not worker_id.strip():
            raise TaskStoreError("worker_id is empty")
        self._validate_lease_seconds(lease_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT status, claimed_by, lease_until
                FROM tasks
                WHERE mission_id = ? AND task_id = ?
                """,
                (mission_id, task_id),
            ).fetchone()
            if row is None:
                db.rollback()
                raise TaskStoreError(f"unknown task: {mission_id}/{task_id}")
            if row["status"] != "running" or row["claimed_by"] != worker_id:
                db.rollback()
                raise TaskStoreError(
                    f"worker does not own running task: {mission_id}/{task_id}"
                )
            if row["lease_until"] is not None and row["lease_until"] <= self._now():
                db.rollback()
                raise TaskStoreError(f"task lease has expired: {mission_id}/{task_id}")

            lease_until = self._lease_until(lease_seconds)
            now = self._now()
            db.execute(
                """
                UPDATE tasks
                SET lease_until = ?, updated_at = ?
                WHERE mission_id = ? AND task_id = ? AND status = 'running'
                  AND claimed_by = ?
                """,
                (lease_until, now, mission_id, task_id, worker_id),
            )
            self._event_locked(
                db,
                mission_id,
                task_id,
                "lease_renewed",
                {"worker_id": worker_id, "lease_until": lease_until},
            )
            db.commit()
            return lease_until

    def recover_expired(
        self,
        mission_id: str,
        *,
        now: str | None = None,
    ) -> int:
        """Requeue running tasks whose worker lease has expired."""
        reference = now or self._now()
        recovered = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT task_id, claimed_by, lease_until
                FROM tasks
                WHERE mission_id = ? AND status = 'running'
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY created_at, task_id
                """,
                (mission_id, reference),
            ).fetchall()
            for row in rows:
                current = self._now()
                updated = db.execute(
                    """
                    UPDATE tasks
                    SET status = 'pending',
                        claimed_by = NULL,
                        lease_until = NULL,
                        updated_at = ?
                    WHERE mission_id = ? AND task_id = ? AND status = 'running'
                      AND (lease_until IS NULL OR lease_until <= ?)
                    """,
                    (current, mission_id, row["task_id"], reference),
                )
                if updated.rowcount != 1:
                    continue
                self._event_locked(
                    db,
                    mission_id,
                    row["task_id"],
                    "requeued",
                    {
                        "reason": "lease_expired",
                        "previous_worker_id": row["claimed_by"],
                        "previous_lease_until": row["lease_until"],
                    },
                )
                recovered += 1
            db.commit()
        return recovered

    def retry(
        self,
        mission_id: str,
        task_id: str,
        *,
        worker_id: str,
        error: str,
    ) -> None:
        """Return a failed running attempt to pending for another attempt."""
        if not worker_id.strip():
            raise TaskStoreError("worker_id is empty")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT status, claimed_by, lease_until
                FROM tasks
                WHERE mission_id = ? AND task_id = ?
                """,
                (mission_id, task_id),
            ).fetchone()
            if row is None:
                db.rollback()
                raise TaskStoreError(f"unknown task: {mission_id}/{task_id}")
            if row["status"] != "running" or row["claimed_by"] != worker_id:
                db.rollback()
                raise TaskStoreError(
                    f"worker does not own running task: {mission_id}/{task_id}"
                )
            now = self._now()
            db.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    last_error = ?,
                    claimed_by = NULL,
                    lease_until = NULL,
                    updated_at = ?
                WHERE mission_id = ? AND task_id = ? AND status = 'running'
                  AND claimed_by = ?
                """,
                (error, now, mission_id, task_id, worker_id),
            )
            self._event_locked(
                db,
                mission_id,
                task_id,
                "retry_scheduled",
                {"worker_id": worker_id, "error": error},
            )
            db.commit()

    def complete(
        self,
        mission_id: str,
        task_id: str,
        *,
        status: str,
        error: str | None = None,
        payload: dict | None = None,
        worker_id: str | None = None,
    ) -> None:
        if status not in {"succeeded", "failed", "blocked"}:
            raise TaskStoreError(f"invalid terminal task status: {status}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT status, claimed_by, lease_until
                FROM tasks
                WHERE mission_id = ? AND task_id = ?
                """,
                (mission_id, task_id),
            ).fetchone()
            if row is None:
                db.rollback()
                raise TaskStoreError(f"unknown task: {mission_id}/{task_id}")
            if row["status"] != "running":
                db.rollback()
                raise TaskStoreError(
                    f"task is not running: {mission_id}/{task_id} ({row['status']})"
                )
            if worker_id is not None and row["claimed_by"] != worker_id:
                db.rollback()
                raise TaskStoreError(
                    f"worker does not own running task: {mission_id}/{task_id}"
                )

            now = self._now()
            if row["claimed_by"] is not None and row["lease_until"] is not None and row["lease_until"] <= now:
                db.rollback()
                raise TaskStoreError(f"task lease has expired: {mission_id}/{task_id}")
            db.execute(
                """
                UPDATE tasks
                SET status = ?, last_error = ?, claimed_by = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE mission_id = ? AND task_id = ?
                """,
                (status, error, now, mission_id, task_id),
            )
            event_payload = dict(payload or {})
            if error:
                event_payload["error"] = error
            if worker_id is not None:
                event_payload["worker_id"] = worker_id
            self._event_locked(db, mission_id, task_id, status, event_payload)
            db.commit()

    def block_dependents(self, mission_id: str) -> int:
        blocked = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM tasks WHERE mission_id = ? AND status = 'pending'",
                (mission_id,),
            ).fetchall()
            for row in rows:
                dependencies = tuple(json.loads(row["deps_json"]))
                failed = any(
                    self._dependency_terminal_unsuccessfully(db, mission_id, dep)
                    for dep in dependencies
                )
                if not failed:
                    continue
                now = self._now()
                db.execute(
                    """
                    UPDATE tasks
                    SET status = 'blocked',
                        last_error = ?,
                        claimed_by = NULL,
                        lease_until = NULL,
                        updated_at = ?
                    WHERE mission_id = ? AND task_id = ? AND status = 'pending'
                    """,
                    (
                        "dependency did not succeed",
                        now,
                        mission_id,
                        row["task_id"],
                    ),
                )
                self._event_locked(
                    db,
                    mission_id,
                    row["task_id"],
                    "blocked",
                    {"reason": "dependency did not succeed"},
                )
                blocked += 1
            db.commit()
        return blocked

    def get(self, mission_id: str, task_id: str) -> StoredTask | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE mission_id = ? AND task_id = ?",
                (mission_id, task_id),
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, mission_id: str) -> tuple[StoredTask, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM tasks WHERE mission_id = ? ORDER BY created_at, task_id",
                (mission_id,),
            ).fetchall()
        return tuple(self._row_to_task(row) for row in rows)

    def events(self, mission_id: str) -> tuple[TaskEvent, ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT event_id, mission_id, task_id, event_type, payload_json, created_at
                FROM task_events
                WHERE mission_id = ?
                ORDER BY event_id
                """,
                (mission_id,),
            ).fetchall()
        return tuple(
            TaskEvent(
                event_id=row["event_id"],
                mission_id=row["mission_id"],
                task_id=row["task_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        )

    @staticmethod
    def _dependency_succeeded(
        db: sqlite3.Connection, mission_id: str, task_id: str
    ) -> bool:
        row = db.execute(
            "SELECT status FROM tasks WHERE mission_id = ? AND task_id = ?",
            (mission_id, task_id),
        ).fetchone()
        if row is None:
            raise TaskStoreError(f"dependency is missing: {mission_id}/{task_id}")
        return row["status"] == "succeeded"

    @staticmethod
    def _dependency_terminal_unsuccessfully(
        db: sqlite3.Connection, mission_id: str, task_id: str
    ) -> bool:
        row = db.execute(
            "SELECT status FROM tasks WHERE mission_id = ? AND task_id = ?",
            (mission_id, task_id),
        ).fetchone()
        if row is None:
            raise TaskStoreError(f"dependency is missing: {mission_id}/{task_id}")
        return row["status"] in {"failed", "blocked"}

    @staticmethod
    def _row_to_task(
        row: sqlite3.Row,
        *,
        status: str | None = None,
        attempts: int | None = None,
        last_error: str | None = None,
        claimed_by: str | None = None,
        lease_until: str | None = None,
    ) -> StoredTask:
        task = DelegatedTask(
            task_id=row["task_id"],
            role=row["role"],
            objective=row["objective"],
            capability=row["capability"],
            args=tuple(json.loads(row["args_json"])),
            depends_on=tuple(json.loads(row["deps_json"])),
        )
        return StoredTask(
            mission_id=row["mission_id"],
            task=task,
            status=status if status is not None else row["status"],
            attempts=attempts if attempts is not None else row["attempts"],
            last_error=last_error if last_error is not None else row["last_error"],
            claimed_by=claimed_by if claimed_by is not None else row["claimed_by"],
            lease_until=lease_until if lease_until is not None else row["lease_until"],
        )

    @classmethod
    def _event_locked(
        cls,
        db: sqlite3.Connection,
        mission_id: str,
        task_id: str | None,
        event_type: str,
        payload: dict,
    ) -> None:
        db.execute(
            """
            INSERT INTO task_events (
                mission_id, task_id, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                task_id,
                event_type,
                json.dumps(payload, sort_keys=True),
                cls._now(),
            ),
        )


__all__ = ["DurableTaskStore", "StoredTask", "TaskEvent", "TaskStoreError"]
