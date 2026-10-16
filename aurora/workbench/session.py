"""Durable WorkSession state for Universal Work / Cowork missions."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4


@dataclass(frozen=True)
class WorkSession:
    work_id: str
    mission_id: str
    actor: str
    environment: str
    workspace_root: str
    state: str = "active"
    capability_ids: tuple[str, ...] = ()
    resources: dict[str, object] = field(default_factory=dict)
    workspace_roots: tuple[str, ...] = ()
    capability_grants: tuple[str, ...] = ()
    identity_refs: tuple[str, ...] = ()
    tool_health: dict[str, str] = field(default_factory=dict)
    artifact_ids: tuple[str, ...] = ()
    execution_receipts: tuple[str, ...] = ()
    correlation_ids: tuple[str, ...] = ()
    continuity: dict[str, object] = field(default_factory=dict)
    approval_state: str = "not_required"
    created_at: str = ""
    updated_at: str = ""


class WorkSessionStore:
    """Persist WorkSession metadata without storing secrets.

    All read-modify-write mutations use one SQLite IMMEDIATE transaction so
    concurrent specialist agents cannot overwrite each other's session state.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS work_sessions (work_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS work_session_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    correlation_id TEXT,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS work_session_actions (
                    idempotency_key TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    mission_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('started', 'completed', 'indeterminate')),
                    claim_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_session_actions_work
                ON work_session_actions(work_id, created_at)
                """
            )

    def begin_action(
        self,
        *,
        idempotency_key: str,
        work_id: str,
        mission_id: str,
        capability_id: str,
        request_fingerprint: str,
        replay_safe: bool,
        stale_after_seconds: float = 900.0,
    ) -> dict[str, object]:
        """Claim one Workbench action or return its durable replay decision.

        Side-effecting actions are never re-executed after an ambiguous started
        record. Read-only actions can be reclaimed after a stale claim. This is
        deliberately conservative: avoiding a duplicate external side effect
        takes precedence over pretending an uncertain result succeeded.
        """
        for name, value in (
            ("idempotency_key", idempotency_key),
            ("work_id", work_id),
            ("mission_id", mission_id),
            ("capability_id", capability_id),
            ("request_fingerprint", request_fingerprint),
        ):
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ValueError(f"{name} must be non-empty and contain no null byte")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")

        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with sqlite3.connect(self.path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT work_id, mission_id, capability_id, request_fingerprint,
                       state, claim_token, created_at, updated_at, result_json, error
                FROM work_session_actions
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if row is None:
                claim_token = uuid4().hex
                db.execute(
                    """
                    INSERT INTO work_session_actions (
                        idempotency_key, work_id, mission_id, capability_id,
                        request_fingerprint, state, claim_token, created_at,
                        updated_at, result_json, error
                    ) VALUES (?, ?, ?, ?, ?, 'started', ?, ?, ?, NULL, NULL)
                    """,
                    (
                        idempotency_key,
                        work_id,
                        mission_id,
                        capability_id,
                        request_fingerprint,
                        claim_token,
                        now,
                        now,
                    ),
                )
                return {"state": "claimed", "claim_token": claim_token}

            if (
                row["work_id"] != work_id
                or row["mission_id"] != mission_id
                or row["capability_id"] != capability_id
                or row["request_fingerprint"] != request_fingerprint
            ):
                raise ValueError(
                    "idempotency_key is already bound to a different Workbench action"
                )

            state = str(row["state"])
            if state == "completed":
                return {
                    "state": "completed",
                    "result": json.loads(row["result_json"] or "{}"),
                }
            if state == "indeterminate":
                return {
                    "state": "indeterminate",
                    "error": row["error"] or "previous action outcome is indeterminate",
                }

            try:
                previous_update = datetime.fromisoformat(str(row["updated_at"]))
                if previous_update.tzinfo is None:
                    previous_update = previous_update.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (now_dt - previous_update).total_seconds())
            except (TypeError, ValueError):
                age_seconds = stale_after_seconds + 1.0

            if age_seconds <= stale_after_seconds:
                return {
                    "state": "in_progress",
                    "error": "the idempotent Workbench action is already in progress",
                }

            if replay_safe:
                claim_token = uuid4().hex
                db.execute(
                    """
                    UPDATE work_session_actions
                    SET claim_token = ?, updated_at = ?
                    WHERE idempotency_key = ? AND state = 'started'
                    """,
                    (claim_token, now, idempotency_key),
                )
                return {"state": "claimed", "claim_token": claim_token, "reclaimed": True}

            error = (
                "previous execution exceeded its durable action lease; outcome is unknown; "
                "the side effect was not repeated"
            )
            db.execute(
                """
                UPDATE work_session_actions
                SET state = 'indeterminate', updated_at = ?, error = ?
                WHERE idempotency_key = ? AND state = 'started'
                """,
                (now, error, idempotency_key),
            )
            return {"state": "indeterminate", "error": error}

    def complete_action(
        self,
        *,
        idempotency_key: str,
        claim_token: str,
        result: dict[str, object],
    ) -> None:
        """Durably cache an action result; reject stale claimants."""
        if not idempotency_key.strip() or not claim_token.strip():
            raise ValueError("idempotency_key and claim_token are required")
        result_json = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT state, claim_token FROM work_session_actions
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise KeyError(idempotency_key)
            if row[1] != claim_token:
                raise ValueError("Workbench action claim was superseded")
            if row[0] not in {"started", "indeterminate", "completed"}:
                raise ValueError(f"invalid Workbench action state: {row[0]}")
            if row[0] == "completed":
                return
            db.execute(
                """
                UPDATE work_session_actions
                SET state = 'completed', updated_at = ?, result_json = ?, error = NULL
                WHERE idempotency_key = ? AND claim_token = ?
                """,
                (now, result_json, idempotency_key, claim_token),
            )

    def get_action(self, idempotency_key: str) -> dict[str, object] | None:
        """Inspect durable action state for support/reconciliation."""
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is empty")
        with sqlite3.connect(self.path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT idempotency_key, work_id, mission_id, capability_id,
                       request_fingerprint, state, created_at, updated_at,
                       result_json, error
                FROM work_session_actions
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "idempotency_key": str(row["idempotency_key"]),
            "work_id": str(row["work_id"]),
            "mission_id": str(row["mission_id"]),
            "capability_id": str(row["capability_id"]),
            "request_fingerprint": str(row["request_fingerprint"]),
            "state": str(row["state"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
        }

    def create_or_get(
        self,
        *,
        work_id: str,
        mission_id: str,
        actor: str,
        environment: str,
        workspace_root: str,
    ) -> WorkSession:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM work_sessions WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if row is not None:
                return self._decode(row[0])
            session = WorkSession(
                work_id,
                mission_id,
                actor,
                environment,
                workspace_root,
                workspace_roots=(workspace_root,),
                continuity={"last_state": "created"},
                created_at=now,
                updated_at=now,
            )
            db.execute(
                "INSERT INTO work_sessions(work_id, payload) VALUES (?, ?)",
                (
                    work_id,
                    json.dumps(
                        asdict(session),
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )
            return session

    def record_capability(self, work_id: str, capability_id: str) -> WorkSession:
        if not capability_id.strip():
            raise ValueError("capability_id is empty")
        return self._mutate(
            work_id,
            lambda session: WorkSession(
                **{
                    **asdict(session),
                    "capability_ids": tuple(
                        dict.fromkeys((*session.capability_ids, capability_id.strip()))
                    ),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

    def record_artifact(self, work_id: str, artifact_id: str) -> WorkSession:
        if not artifact_id.strip():
            raise ValueError("artifact_id is empty")
        return self._mutate(
            work_id,
            lambda session: WorkSession(
                **{
                    **asdict(session),
                    "artifact_ids": tuple(
                        dict.fromkeys((*session.artifact_ids, artifact_id.strip()))
                    ),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

    def update_tool_health(
        self,
        work_id: str,
        capability_id: str,
        state: str,
    ) -> WorkSession:
        if not capability_id.strip() or not state.strip():
            raise ValueError("capability_id and state are required")
        def mutate(session: WorkSession) -> WorkSession:
            health = dict(session.tool_health)
            health[capability_id.strip()] = state.strip()
            return WorkSession(
                **{
                    **asdict(session),
                    "tool_health": health,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return self._mutate(work_id, mutate)

    def set_approval_state(self, work_id: str, state: str) -> WorkSession:
        if not state.strip():
            raise ValueError("approval state is empty")
        return self._mutate(
            work_id,
            lambda session: WorkSession(
                **{
                    **asdict(session),
                    "approval_state": state.strip(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

    def record_receipt(
        self,
        work_id: str,
        receipt_id: str,
        *,
        correlation_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> WorkSession:
        if not receipt_id.strip():
            raise ValueError("receipt_id is empty")

        now = datetime.now(timezone.utc).isoformat()

        def mutate(session: WorkSession) -> WorkSession:
            receipt_ids = tuple(
                dict.fromkeys((*session.execution_receipts, receipt_id.strip()))
            )
            correlation_ids = (
                tuple(dict.fromkeys((*session.correlation_ids, correlation_id)))
                if correlation_id
                else session.correlation_ids
            )
            return WorkSession(
                **{
                    **asdict(session),
                    "execution_receipts": receipt_ids,
                    "correlation_ids": correlation_ids,
                    "continuity": {
                        **session.continuity,
                        "last_receipt_id": receipt_id.strip(),
                    },
                    "updated_at": now,
                }
            )

        def side_effect(db: sqlite3.Connection) -> None:
            db.execute(
                """
                INSERT OR REPLACE INTO work_session_receipts (
                    receipt_id, work_id, correlation_id, created_at, payload
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    receipt_id.strip(),
                    work_id,
                    correlation_id,
                    now,
                    json.dumps(
                        payload or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )

        return self._mutate(work_id, mutate, side_effect=side_effect)

    def get_receipts(self, work_id: str) -> tuple[dict[str, object], ...]:
        with sqlite3.connect(self.path, timeout=30) as db:
            rows = db.execute(
                """
                SELECT receipt_id, correlation_id, created_at, payload
                FROM work_session_receipts
                WHERE work_id = ?
                ORDER BY created_at, receipt_id
                """,
                (work_id,),
            ).fetchall()
        return tuple(
            {
                "receipt_id": row[0],
                "work_id": work_id,
                "correlation_id": row[1],
                "created_at": row[2],
                "payload": json.loads(row[3]),
            }
            for row in rows
        )

    def get(self, work_id: str) -> WorkSession:
        with sqlite3.connect(self.path, timeout=30) as db:
            row = db.execute(
                "SELECT payload FROM work_sessions WHERE work_id = ?",
                (work_id,),
            ).fetchone()
        if row is None:
            raise KeyError(work_id)
        return self._decode(row[0])

    def close(self, work_id: str) -> WorkSession:
        return self._mutate(
            work_id,
            lambda session: WorkSession(
                **{
                    **asdict(session),
                    "state": "closed",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )

    def _mutate(
        self,
        work_id: str,
        transform: Callable[[WorkSession], WorkSession],
        *,
        side_effect: Callable[[sqlite3.Connection], None] | None = None,
    ) -> WorkSession:
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM work_sessions WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if row is None:
                raise KeyError(work_id)
            session = self._decode(row[0])
            updated = transform(session)
            db.execute(
                "UPDATE work_sessions SET payload = ? WHERE work_id = ?",
                (
                    json.dumps(
                        asdict(updated),
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                    work_id,
                ),
            )
            if side_effect is not None:
                side_effect(db)
            return updated

    def _save(self, session: WorkSession) -> None:
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute(
                "UPDATE work_sessions SET payload = ? WHERE work_id = ?",
                (
                    json.dumps(
                        asdict(session),
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                    session.work_id,
                ),
            )

    @staticmethod
    def _decode(payload: str) -> WorkSession:
        value = json.loads(payload)
        return WorkSession(
            work_id=value["work_id"],
            mission_id=value["mission_id"],
            actor=value["actor"],
            environment=value["environment"],
            workspace_root=value["workspace_root"],
            state=value["state"],
            capability_ids=tuple(value.get("capability_ids", ())),
            resources=dict(value.get("resources", {})),
            workspace_roots=tuple(value.get("workspace_roots", ())),
            capability_grants=tuple(value.get("capability_grants", ())),
            identity_refs=tuple(value.get("identity_refs", ())),
            tool_health=dict(value.get("tool_health", {})),
            artifact_ids=tuple(value.get("artifact_ids", ())),
            execution_receipts=tuple(value.get("execution_receipts", ())),
            correlation_ids=tuple(value.get("correlation_ids", ())),
            continuity=dict(value.get("continuity", {})),
            approval_state=str(value.get("approval_state", "not_required")),
            created_at=value["created_at"],
            updated_at=value["updated_at"],
        )


__all__ = ["WorkSession", "WorkSessionStore"]
