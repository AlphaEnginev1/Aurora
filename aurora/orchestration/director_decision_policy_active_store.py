"""Durable active-policy registry for governed Director policy changes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from pathlib import Path


class DirectorDecisionPolicyActiveStoreError(ValueError):
    """Raised when active policy state violates governance."""


@dataclass(frozen=True)
class DirectorDecisionPolicyActive:
    proposal_id: str
    validation_id: str
    authorized_by: str
    activated_at: str = ""

    def __post_init__(self) -> None:
        if not self.proposal_id.strip() or not self.validation_id.strip():
            raise DirectorDecisionPolicyActiveStoreError(
                "active policy requires proposal and validation ids"
            )
        if not self.authorized_by.strip():
            raise DirectorDecisionPolicyActiveStoreError(
                "active policy requires an authority"
            )
        if not self.activated_at:
            object.__setattr__(
                self,
                "activated_at",
                datetime.now(timezone.utc).isoformat(),
            )


class DirectorDecisionPolicyActiveStore:
    """Keep exactly one durable active proposal and preserve prior versions."""

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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS director_decision_policy_active_history (
                    activation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT NOT NULL,
                    validation_id TEXT NOT NULL,
                    authorized_by TEXT NOT NULL,
                    activated_at TEXT NOT NULL,
                    deactivated_at TEXT,
                    deactivation_reason TEXT
                )
                """
            )
            db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_director_policy_one_active
                ON director_decision_policy_active_history(proposal_id, deactivated_at)
                WHERE deactivated_at IS NULL
                """
            )

    def activate(self, active: DirectorDecisionPolicyActive) -> None:
        with self._connect() as db:
            current = db.execute(
                """
                SELECT activation_id, proposal_id FROM director_decision_policy_active_history
                WHERE deactivated_at IS NULL
                ORDER BY activation_id DESC
                LIMIT 1
                """
            ).fetchone()
            if current is not None:
                if str(current["proposal_id"]) == active.proposal_id:
                    raise DirectorDecisionPolicyActiveStoreError(
                        "Director policy proposal is already active"
                    )
                raise DirectorDecisionPolicyActiveStoreError(
                    "an active Director policy already exists"
                )
            db.execute(
                """
                INSERT INTO director_decision_policy_active_history (
                    proposal_id, validation_id, authorized_by, activated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    active.proposal_id,
                    active.validation_id,
                    active.authorized_by,
                    active.activated_at,
                ),
            )

    def current(self) -> DirectorDecisionPolicyActive | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT proposal_id, validation_id, authorized_by, activated_at
                FROM director_decision_policy_active_history
                WHERE deactivated_at IS NULL
                ORDER BY activation_id DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row(row) if row is not None else None

    def deactivate(self, *, proposal_id: str, reason: str, deactivated_at: str | None = None) -> None:
        if not proposal_id.strip():
            raise DirectorDecisionPolicyActiveStoreError("proposal id cannot be empty")
        if not reason.strip():
            raise DirectorDecisionPolicyActiveStoreError("deactivation requires a reason")
        timestamp = deactivated_at or datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            result = db.execute(
                """
                UPDATE director_decision_policy_active_history
                SET deactivated_at=?, deactivation_reason=?
                WHERE proposal_id=? AND deactivated_at IS NULL
                """,
                (timestamp, reason.strip(), proposal_id),
            )
            if result.rowcount == 0:
                raise DirectorDecisionPolicyActiveStoreError(
                    "no active policy exists for proposal"
                )

    def history(self) -> tuple[DirectorDecisionPolicyActive, ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT proposal_id, validation_id, authorized_by, activated_at
                FROM director_decision_policy_active_history
                ORDER BY activation_id
                """
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> DirectorDecisionPolicyActive:
        return DirectorDecisionPolicyActive(
            proposal_id=str(row["proposal_id"]),
            validation_id=str(row["validation_id"]),
            authorized_by=str(row["authorized_by"]),
            activated_at=str(row["activated_at"]),
        )


__all__ = [
    "DirectorDecisionPolicyActive",
    "DirectorDecisionPolicyActiveStore",
    "DirectorDecisionPolicyActiveStoreError",
]
