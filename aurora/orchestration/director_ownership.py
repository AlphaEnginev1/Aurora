"""Durable mission-level Director ownership and fencing authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import uuid


class DirectorOwnershipError(RuntimeError):
    """Raised when Director mission ownership cannot be advanced safely."""


class DirectorOwnershipRejected(DirectorOwnershipError):
    """Raised when ownership is held by another Director or fencing is stale."""


@dataclass(frozen=True)
class DirectorOwnership:
    mission_id: str
    owner_id: str
    fencing_epoch: int
    lease_until: str
    acquired_at: str
    updated_at: str


class DirectorOwnershipStore:
    """SQLite-backed mission ownership with atomic lease and fencing operations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS director_ownership (
                    mission_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    fencing_epoch INTEGER NOT NULL,
                    lease_until TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_director_ownership_lease
                    ON director_ownership (lease_until);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _lease_until(seconds: float) -> str:
        if seconds <= 0:
            raise DirectorOwnershipError("lease_seconds must be positive")
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    @staticmethod
    def _row_to_ownership(row: sqlite3.Row) -> DirectorOwnership:
        return DirectorOwnership(
            mission_id=row["mission_id"],
            owner_id=row["owner_id"],
            fencing_epoch=int(row["fencing_epoch"]),
            lease_until=row["lease_until"],
            acquired_at=row["acquired_at"],
            updated_at=row["updated_at"],
        )

    def get(self, mission_id: str) -> DirectorOwnership | None:
        if not mission_id.strip():
            raise DirectorOwnershipError("mission_id is empty")
        with self._connect() as db:
            row = db.execute(
                """
                SELECT mission_id, owner_id, fencing_epoch, lease_until, acquired_at, updated_at
                FROM director_ownership
                WHERE mission_id = ?
                """,
                (mission_id,),
            ).fetchone()
        return self._row_to_ownership(row) if row is not None else None

    def acquire(
        self,
        mission_id: str,
        owner_id: str,
        *,
        lease_seconds: float = 30.0,
        now: str | None = None,
        correlation_id: str | None = None,
    ) -> DirectorOwnership:
        if not mission_id.strip():
            raise DirectorOwnershipError("mission_id is empty")
        if not owner_id.strip():
            raise DirectorOwnershipError("owner_id is empty")
        if lease_seconds <= 0:
            raise DirectorOwnershipError("lease_seconds must be positive")

        reference = now or self._now()
        lease_until = (
            datetime.fromisoformat(reference) + timedelta(seconds=lease_seconds)
        ).isoformat()

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM director_ownership WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()

            if current is not None:
                current_ownership = self._row_to_ownership(current)
                if current_ownership.lease_until > reference:
                    db.rollback()
                    raise DirectorOwnershipRejected(
                        f"mission ownership is held by {current_ownership.owner_id}"
                    )
                epoch = current_ownership.fencing_epoch + 1
            else:
                epoch = 1

            updated_at = reference
            db.execute(
                """
                INSERT INTO director_ownership (
                    mission_id, owner_id, fencing_epoch,
                    lease_until, acquired_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    fencing_epoch=excluded.fencing_epoch,
                    lease_until=excluded.lease_until,
                    acquired_at=excluded.acquired_at,
                    updated_at=excluded.updated_at
                """,
                (
                    mission_id,
                    owner_id,
                    epoch,
                    lease_until,
                    reference,
                    updated_at,
                ),
            )
            db.commit()

        return DirectorOwnership(
            mission_id=mission_id,
            owner_id=owner_id,
            fencing_epoch=epoch,
            lease_until=lease_until,
            acquired_at=reference,
            updated_at=updated_at,
        )

    def renew(
        self,
        mission_id: str,
        owner_id: str,
        fencing_epoch: int,
        *,
        lease_seconds: float = 30.0,
        now: str | None = None,
    ) -> DirectorOwnership:
        if fencing_epoch < 1:
            raise DirectorOwnershipError("fencing_epoch must be positive")
        if not mission_id.strip():
            raise DirectorOwnershipError("mission_id is empty")
        if not owner_id.strip():
            raise DirectorOwnershipError("owner_id is empty")
        if lease_seconds <= 0:
            raise DirectorOwnershipError("lease_seconds must be positive")

        reference = now or self._now()
        lease_until = (
            datetime.fromisoformat(reference) + timedelta(seconds=lease_seconds)
        ).isoformat()

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM director_ownership WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise DirectorOwnershipRejected("mission has no Director owner")

            current = self._row_to_ownership(row)
            if (
                current.owner_id != owner_id
                or current.fencing_epoch != fencing_epoch
                or current.lease_until <= reference
            ):
                db.rollback()
                raise DirectorOwnershipRejected(
                    f"Director ownership is stale or expired for {mission_id}"
                )

            db.execute(
                """
                UPDATE director_ownership
                SET lease_until = ?, updated_at = ?
                WHERE mission_id = ? AND owner_id = ? AND fencing_epoch = ?
                """,
                (
                    lease_until,
                    reference,
                    mission_id,
                    owner_id,
                    fencing_epoch,
                ),
            )
            db.commit()

        return DirectorOwnership(
            mission_id=mission_id,
            owner_id=owner_id,
            fencing_epoch=fencing_epoch,
            lease_until=lease_until,
            acquired_at=current.acquired_at,
            updated_at=reference,
        )

    def require_owner(
        self,
        mission_id: str,
        owner_id: str,
        fencing_epoch: int,
        *,
        now: str | None = None,
    ) -> DirectorOwnership:
        if fencing_epoch < 1:
            raise DirectorOwnershipError("fencing_epoch must be positive")
        reference = now or self._now()
        current = self.get(mission_id)
        if current is None:
            raise DirectorOwnershipRejected("mission has no Director owner")
        if (
            current.owner_id != owner_id
            or current.fencing_epoch != fencing_epoch
            or current.lease_until <= reference
        ):
            raise DirectorOwnershipRejected(
                f"Director ownership fence rejected for {mission_id}"
            )
        return current

    def release(
        self,
        mission_id: str,
        owner_id: str,
        fencing_epoch: int,
        *,
        now: str | None = None,
    ) -> None:
        reference = now or self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT owner_id, fencing_epoch FROM director_ownership WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise DirectorOwnershipRejected("mission has no Director owner")
            if (
                row["owner_id"] != owner_id
                or int(row["fencing_epoch"]) != fencing_epoch
            ):
                db.rollback()
                raise DirectorOwnershipRejected(
                    f"Director ownership fence rejected for release: {mission_id}"
                )
            updated = db.execute(
                """
                UPDATE director_ownership
                SET lease_until = ?, updated_at = ?
                WHERE mission_id = ? AND owner_id = ? AND fencing_epoch = ?
                """,
                (
                    reference,
                    reference,
                    mission_id,
                    owner_id,
                    fencing_epoch,
                ),
            )
            if updated.rowcount != 1:
                db.rollback()
                raise DirectorOwnershipRejected(
                    f"Director ownership release was not applied: {mission_id}"
                )
            db.commit()

    @staticmethod
    def new_owner_id(prefix: str = "director") -> str:
        if not prefix.strip():
            raise DirectorOwnershipError("owner prefix is empty")
        return f"{prefix}-{uuid.uuid4().hex[:12]}"
