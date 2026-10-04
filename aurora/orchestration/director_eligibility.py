"""Durable authoritative Director eligibility registry.

Eligibility answers whether a stable Director identity may contend for mission
ownership. It never grants mission ownership or live-execution authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import json
import sqlite3
import uuid


class DirectorEligibilityError(RuntimeError):
    """Base error for durable Director eligibility state."""


class DirectorEligibilityUnavailable(DirectorEligibilityError):
    """Raised when the authoritative eligibility source cannot be read safely."""


class DirectorEligibilityAmbiguous(DirectorEligibilityError):
    """Raised when one Director identity has contradictory current records."""


@dataclass(frozen=True)
class DirectorEligibility:
    director_id: str
    generation: int
    state: str
    ready: bool
    required_capabilities: tuple[str, ...]
    registration_at: str
    updated_at: str
    last_seen_at: str
    freshness_seconds: float


@dataclass(frozen=True)
class DirectorEligibilityDecision:
    eligible: bool
    reason: str
    record: DirectorEligibility | None = None


class DirectorEligibilityStore:
    """SQLite-backed authoritative eligibility history.

    A generation normally has exactly one record. Multiple records at the
    same current generation are treated as contradictory and fail closed.
    """

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    REMOVED = "REMOVED"
    STATES = (ELIGIBLE, INELIGIBLE, REMOVED)

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=30000")
            return db
        except sqlite3.Error as exc:
            raise DirectorEligibilityUnavailable(
                "eligibility authority could not be opened"
            ) from exc

    def _initialize(self) -> None:
        try:
            with self._connect() as db:
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS director_eligibility (
                        record_id TEXT PRIMARY KEY,
                        director_id TEXT NOT NULL,
                        generation INTEGER NOT NULL CHECK(generation >= 1),
                        state TEXT NOT NULL CHECK(state IN
                            ('ELIGIBLE','INELIGIBLE','REMOVED')),
                        ready INTEGER NOT NULL CHECK(ready IN (0,1)),
                        required_capabilities TEXT NOT NULL,
                        registration_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        freshness_seconds REAL NOT NULL CHECK(freshness_seconds > 0)
                    );
                    CREATE INDEX IF NOT EXISTS idx_director_eligibility_current
                        ON director_eligibility(director_id, generation);
                    """
                )
        except DirectorEligibilityError:
            raise
        except sqlite3.Error as exc:
            raise DirectorEligibilityUnavailable(
                "eligibility authority could not be initialized"
            ) from exc

    @staticmethod
    def _record(row: sqlite3.Row) -> DirectorEligibility:
        try:
            capabilities = tuple(json.loads(row["required_capabilities"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DirectorEligibilityUnavailable(
                "eligibility record has invalid capability data"
            ) from exc
        if any(not isinstance(item, str) or not item.strip() for item in capabilities):
            raise DirectorEligibilityUnavailable(
                "eligibility record has invalid capability data"
            )
        return DirectorEligibility(
            director_id=row["director_id"],
            generation=int(row["generation"]),
            state=row["state"],
            ready=bool(row["ready"]),
            required_capabilities=tuple(sorted(set(capabilities))),
            registration_at=row["registration_at"],
            updated_at=row["updated_at"],
            last_seen_at=row["last_seen_at"],
            freshness_seconds=float(row["freshness_seconds"]),
        )

    @staticmethod
    def _validate_time(value: str, field: str) -> str:
        if not value.strip():
            raise DirectorEligibilityError(f"{field} is empty")
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise DirectorEligibilityError(f"{field} is not ISO-8601") from exc
        return value

    def register(
        self,
        director_id: str,
        *,
        state: str,
        ready: bool,
        required_capabilities: tuple[str, ...],
        registration_at: str,
        updated_at: str,
        last_seen_at: str,
        freshness_seconds: float,
        generation: int | None = None,
        record_id: str | None = None,
    ) -> DirectorEligibility:
        """Append a durable authoritative record.

        Credentials are deliberately not part of this API or its persisted
        schema.
        """
        if not director_id.strip():
            raise DirectorEligibilityError("director_id is empty")
        if state not in self.STATES:
            raise DirectorEligibilityError(f"unsupported eligibility state: {state}")
        if freshness_seconds <= 0:
            raise DirectorEligibilityError("freshness_seconds must be positive")
        if not required_capabilities:
            raise DirectorEligibilityError("required_capabilities must not be empty")
        capabilities = tuple(sorted(set(required_capabilities)))
        if any(not item.strip() for item in capabilities):
            raise DirectorEligibilityError("required_capabilities contains an empty value")
        self._validate_time(registration_at, "registration_at")
        self._validate_time(updated_at, "updated_at")
        self._validate_time(last_seen_at, "last_seen_at")

        identifier = record_id or f"eligibility-{uuid.uuid4().hex}"
        if not identifier.strip():
            raise DirectorEligibilityError("record_id is empty")

        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if generation is None:
                    row = db.execute(
                        "SELECT COALESCE(MAX(generation), 0) AS generation "
                        "FROM director_eligibility WHERE director_id = ?",
                        (director_id,),
                    ).fetchone()
                    generation_value = int(row["generation"]) + 1
                else:
                    if generation < 1:
                        raise DirectorEligibilityError("generation must be positive")
                    generation_value = generation

                db.execute(
                    """
                    INSERT INTO director_eligibility (
                        record_id, director_id, generation, state, ready,
                        required_capabilities, registration_at, updated_at,
                        last_seen_at, freshness_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        director_id,
                        generation_value,
                        state,
                        int(ready),
                        json.dumps(capabilities, separators=(",", ":")),
                        registration_at,
                        updated_at,
                        last_seen_at,
                        freshness_seconds,
                    ),
                )
                db.commit()
        except DirectorEligibilityError:
            raise
        except sqlite3.Error as exc:
            raise DirectorEligibilityUnavailable(
                "eligibility record could not be persisted"
            ) from exc

        return DirectorEligibility(
            director_id=director_id,
            generation=generation_value,
            state=state,
            ready=bool(ready),
            required_capabilities=capabilities,
            registration_at=registration_at,
            updated_at=updated_at,
            last_seen_at=last_seen_at,
            freshness_seconds=float(freshness_seconds),
        )

    def current(self, director_id: str) -> DirectorEligibility | None:
        if not director_id.strip():
            raise DirectorEligibilityError("director_id is empty")
        try:
            with self._connect() as db:
                rows = db.execute(
                    """
                    SELECT * FROM director_eligibility
                    WHERE director_id = ?
                    ORDER BY generation DESC
                    """,
                    (director_id,),
                ).fetchall()
        except DirectorEligibilityError:
            raise
        except sqlite3.Error as exc:
            raise DirectorEligibilityUnavailable(
                "eligibility authority could not be read"
            ) from exc

        if not rows:
            return None
        latest_generation = int(rows[0]["generation"])
        latest = [row for row in rows if int(row["generation"]) == latest_generation]
        if len(latest) != 1:
            raise DirectorEligibilityAmbiguous(
                f"contradictory eligibility records for {director_id}"
            )
        return self._record(latest[0])

    def identities(self) -> tuple[str, ...]:
        try:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT DISTINCT director_id FROM director_eligibility "
                    "ORDER BY director_id"
                ).fetchall()
        except DirectorEligibilityError:
            raise
        except sqlite3.Error as exc:
            raise DirectorEligibilityUnavailable(
                "eligibility authority could not list identities"
            ) from exc
        return tuple(row["director_id"] for row in rows)

    def resolve_unique_identity(self) -> str:
        identities = self.identities()
        current = []
        for director_id in identities:
            record = self.current(director_id)
            if record is not None and record.state != self.REMOVED:
                current.append(record)
        if len(current) == 1:
            return current[0].director_id
        if not current:
            raise DirectorEligibilityError(
                "no unique durable Director identity is registered"
            )
        raise DirectorEligibilityAmbiguous(
            "multiple durable Director identities are registered"
        )

    def evaluate(
        self,
        director_id: str,
        *,
        now: str,
        required_capabilities: tuple[str, ...],
    ) -> DirectorEligibilityDecision:
        self._validate_time(now, "now")
        try:
            record = self.current(director_id)
        except DirectorEligibilityError:
            raise
        except Exception as exc:
            raise DirectorEligibilityUnavailable(
                "eligibility evaluation failed"
            ) from exc

        if record is None:
            return DirectorEligibilityDecision(
                False, "no_authoritative_eligibility_record"
            )
        if record.state != self.ELIGIBLE:
            return DirectorEligibilityDecision(
                False, f"explicit_state_{record.state.lower()}", record
            )
        if not record.ready:
            return DirectorEligibilityDecision(False, "director_not_ready", record)

        required = set(required_capabilities)
        capabilities = set(record.required_capabilities)
        missing = sorted(required - capabilities)
        if missing:
            return DirectorEligibilityDecision(
                False,
                "missing_required_capabilities:" + ",".join(missing),
                record,
            )

        reference = datetime.fromisoformat(now)
        last_seen = datetime.fromisoformat(record.last_seen_at)
        if reference > last_seen + timedelta(seconds=record.freshness_seconds):
            return DirectorEligibilityDecision(False, "eligibility_record_stale", record)

        return DirectorEligibilityDecision(True, "eligible", record)
