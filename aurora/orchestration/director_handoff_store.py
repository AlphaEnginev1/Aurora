"""Durable cooperative Director handoff records and atomic state transitions.

This store is intentionally independent of DirectorRuntime. Runtime integration is
introduced in the next Increment 11 block after this state machine is validated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


class DirectorHandoffError(RuntimeError):
    """Base error for durable handoff state."""


class DirectorHandoffRejected(DirectorHandoffError):
    """Raised when a handoff transition violates its state or fencing contract."""


@dataclass(frozen=True)
class DirectorHandoff:
    handoff_id: str
    mission_id: str
    predecessor_id: str
    successor_id: str
    predecessor_epoch: int
    state: str
    version: int
    created_at: str
    updated_at: str
    released_at: str | None = None
    successor_epoch: int | None = None


@dataclass(frozen=True)
class DirectorHandoffEvent:
    sequence: int
    handoff_id: str
    mission_id: str
    event_type: str
    from_state: str | None
    to_state: str
    actor_id: str
    fencing_epoch: int
    occurred_at: str


class DirectorHandoffStore:
    """SQLite-backed append-only handoff history with CAS state transitions."""

    ACTIVE_STATES = ("REQUESTED", "QUIESCING", "RELEASED", "ACQUIRED")
    TERMINAL_STATES = ("COMPLETED", "ABORTED")

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS director_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    predecessor_id TEXT NOT NULL,
                    successor_id TEXT NOT NULL,
                    predecessor_epoch INTEGER NOT NULL CHECK(predecessor_epoch > 0),
                    state TEXT NOT NULL CHECK(state IN
                        ('REQUESTED','QUIESCING','RELEASED','ACQUIRED','COMPLETED','ABORTED')),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    released_at TEXT,
                    successor_epoch INTEGER,
                    CHECK(predecessor_id <> successor_id),
                    CHECK(successor_epoch IS NULL OR successor_epoch > predecessor_epoch)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_director_handoff_active_mission
                    ON director_handoffs(mission_id)
                    WHERE state IN ('REQUESTED','QUIESCING','RELEASED','ACQUIRED');
                CREATE TABLE IF NOT EXISTS director_handoff_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    handoff_id TEXT NOT NULL REFERENCES director_handoffs(handoff_id),
                    mission_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    fencing_epoch INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_director_handoff_events
                    ON director_handoff_events(handoff_id, sequence);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _record(row: sqlite3.Row | None) -> DirectorHandoff | None:
        if row is None:
            return None
        return DirectorHandoff(
            handoff_id=row["handoff_id"],
            mission_id=row["mission_id"],
            predecessor_id=row["predecessor_id"],
            successor_id=row["successor_id"],
            predecessor_epoch=int(row["predecessor_epoch"]),
            state=row["state"],
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            released_at=row["released_at"],
            successor_epoch=(int(row["successor_epoch"]) if row["successor_epoch"] is not None else None),
        )

    def get(self, handoff_id: str) -> DirectorHandoff | None:
        if not handoff_id.strip():
            raise DirectorHandoffError("handoff_id is empty")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM director_handoffs WHERE handoff_id = ?", (handoff_id,)
            ).fetchone()
        return self._record(row)

    def get_active_for_mission(self, mission_id: str) -> DirectorHandoff | None:
        if not mission_id.strip():
            raise DirectorHandoffError("mission_id is empty")
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM director_handoffs WHERE mission_id = ?
                   AND state IN ('REQUESTED','QUIESCING','RELEASED','ACQUIRED')""",
                (mission_id,),
            ).fetchone()
        return self._record(row)

    def events(self, handoff_id: str) -> tuple[DirectorHandoffEvent, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM director_handoff_events
                   WHERE handoff_id = ? ORDER BY sequence""",
                (handoff_id,),
            ).fetchall()
        return tuple(DirectorHandoffEvent(
            sequence=int(row["sequence"]), handoff_id=row["handoff_id"],
            mission_id=row["mission_id"], event_type=row["event_type"],
            from_state=row["from_state"], to_state=row["to_state"],
            actor_id=row["actor_id"], fencing_epoch=int(row["fencing_epoch"]),
            occurred_at=row["occurred_at"],
        ) for row in rows)

    def request(
        self, mission_id: str, predecessor_id: str, successor_id: str,
        predecessor_epoch: int, *, handoff_id: str | None = None, now: str | None = None,
    ) -> DirectorHandoff:
        if not mission_id.strip() or not predecessor_id.strip() or not successor_id.strip():
            raise DirectorHandoffError("mission and Director identities must be non-empty")
        if predecessor_id == successor_id:
            raise DirectorHandoffError("successor must differ from predecessor")
        if predecessor_epoch < 1:
            raise DirectorHandoffError("predecessor_epoch must be positive")
        reference = now or self._now()
        identifier = handoff_id or f"handoff-{uuid.uuid4().hex}"
        if not identifier.strip():
            raise DirectorHandoffError("handoff_id is empty")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO director_handoffs
                       (handoff_id, mission_id, predecessor_id, successor_id,
                        predecessor_epoch, state, version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'REQUESTED', 1, ?, ?)""",
                    (identifier, mission_id, predecessor_id, successor_id,
                     predecessor_epoch, reference, reference),
                )
                db.execute(
                    """INSERT INTO director_handoff_events
                       (handoff_id, mission_id, event_type, from_state, to_state,
                        actor_id, fencing_epoch, occurred_at)
                       VALUES (?, ?, 'HANDOFF_REQUESTED', NULL, 'REQUESTED', ?, ?, ?)""",
                    (identifier, mission_id, predecessor_id, predecessor_epoch, reference),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise DirectorHandoffRejected(
                    "handoff already exists or another handoff is active for this mission"
                ) from exc
        return self.get(identifier)  # type: ignore[return-value]

    def _transition(
        self, handoff_id: str, expected_state: str, next_state: str,
        event_type: str, actor_id: str, fencing_epoch: int, *,
        now: str | None = None, successor_epoch: int | None = None,
    ) -> DirectorHandoff:
        if not handoff_id.strip() or not actor_id.strip():
            raise DirectorHandoffError("handoff_id and actor_id must be non-empty")
        reference = now or self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM director_handoffs WHERE handoff_id = ?", (handoff_id,)
            ).fetchone()
            if row is None:
                db.rollback()
                raise DirectorHandoffRejected("handoff does not exist")
            current = self._record(row)
            assert current is not None
            if current.state != expected_state:
                db.rollback()
                raise DirectorHandoffRejected(
                    f"expected {expected_state}, found {current.state}"
                )
            expected_actor = current.successor_id if next_state in ("ACQUIRED", "COMPLETED") else current.predecessor_id
            if actor_id != expected_actor or fencing_epoch != current.predecessor_epoch:
                db.rollback()
                raise DirectorHandoffRejected("actor identity or predecessor fencing epoch mismatch")
            if next_state == "ACQUIRED":
                if successor_epoch is None or successor_epoch <= current.predecessor_epoch:
                    db.rollback()
                    raise DirectorHandoffRejected("successor epoch must exceed predecessor epoch")
            if next_state == "COMPLETED" and successor_epoch != current.successor_epoch:
                db.rollback()
                raise DirectorHandoffRejected("successor fencing epoch mismatch")
            updated = db.execute(
                """UPDATE director_handoffs
                   SET state = ?, version = version + 1, updated_at = ?,
                       released_at = CASE WHEN ? = 'RELEASED' THEN ? ELSE released_at END,
                       successor_epoch = CASE WHEN ? = 'ACQUIRED' THEN ? ELSE successor_epoch END
                   WHERE handoff_id = ? AND state = ? AND version = ?""",
                (next_state, reference, next_state, reference, next_state,
                 successor_epoch, handoff_id, expected_state, current.version),
            )
            if updated.rowcount != 1:
                db.rollback()
                raise DirectorHandoffRejected("handoff transition lost its compare-and-set")
            db.execute(
                """INSERT INTO director_handoff_events
                   (handoff_id, mission_id, event_type, from_state, to_state,
                    actor_id, fencing_epoch, occurred_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (handoff_id, current.mission_id, event_type, expected_state,
                 next_state, actor_id, fencing_epoch, reference),
            )
            db.commit()
        return self.get(handoff_id)  # type: ignore[return-value]

    def begin_quiescing(self, handoff_id: str, predecessor_id: str,
                        predecessor_epoch: int, *, now: str | None = None) -> DirectorHandoff:
        return self._transition(handoff_id, "REQUESTED", "QUIESCING",
                                "HANDOFF_QUIESCING", predecessor_id,
                                predecessor_epoch, now=now)

    def mark_released(self, handoff_id: str, predecessor_id: str,
                      predecessor_epoch: int, *, now: str | None = None) -> DirectorHandoff:
        return self._transition(handoff_id, "QUIESCING", "RELEASED",
                                "HANDOFF_RELEASED", predecessor_id,
                                predecessor_epoch, now=now)

    def mark_acquired(self, handoff_id: str, successor_id: str,
                      predecessor_epoch: int, successor_epoch: int, *,
                      now: str | None = None) -> DirectorHandoff:
        # The event records the successor as actor; predecessor epoch remains the
        # handoff's authority epoch, while successor_epoch proves fenced takeover.
        record = self.get(handoff_id)
        if record is None or successor_id != record.successor_id:
            raise DirectorHandoffRejected("only designated successor may acquire handoff")
        return self._transition(handoff_id, "RELEASED", "ACQUIRED",
                                "HANDOFF_SUCCESSOR_ACQUIRED", successor_id,
                                predecessor_epoch, now=now,
                                successor_epoch=successor_epoch)

    def complete(self, handoff_id: str, successor_id: str,
                 predecessor_epoch: int, successor_epoch: int, *,
                 now: str | None = None) -> DirectorHandoff:
        record = self.get(handoff_id)
        if record is None or record.successor_id != successor_id:
            raise DirectorHandoffRejected("only designated successor may complete handoff")
        if record.successor_epoch != successor_epoch:
            raise DirectorHandoffRejected("successor fencing epoch mismatch")
        # Completion is a handoff lifecycle marker, not a mission terminal outcome.
        return self._transition(handoff_id, "ACQUIRED", "COMPLETED",
                                "HANDOFF_COMPLETED", successor_id,
                                predecessor_epoch, now=now,
                                successor_epoch=successor_epoch)
