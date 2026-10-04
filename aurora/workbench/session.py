"""Durable WorkSession state for Universal Work / Cowork missions."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


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
    created_at: str = ""
    updated_at: str = ""


class WorkSessionStore:
    """Persist WorkSession metadata without storing secrets."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS work_sessions (work_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def create_or_get(self, *, work_id: str, mission_id: str, actor: str, environment: str, workspace_root: str) -> WorkSession:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM work_sessions WHERE work_id = ?", (work_id,)).fetchone()
            if row is not None:
                return self._decode(row[0])
            session = WorkSession(work_id, mission_id, actor, environment, workspace_root, created_at=now, updated_at=now)
            db.execute("INSERT INTO work_sessions(work_id, payload) VALUES (?, ?)", (work_id, json.dumps(asdict(session), sort_keys=True)))
            return session

    def record_capability(self, work_id: str, capability_id: str) -> WorkSession:
        session = self.get(work_id)
        capabilities = tuple(dict.fromkeys((*session.capability_ids, capability_id)))
        updated = WorkSession(**{**asdict(session), "capability_ids": capabilities, "updated_at": datetime.now(timezone.utc).isoformat()})
        self._save(updated)
        return updated

    def get(self, work_id: str) -> WorkSession:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM work_sessions WHERE work_id = ?", (work_id,)).fetchone()
        if row is None:
            raise KeyError(work_id)
        return self._decode(row[0])

    def close(self, work_id: str) -> WorkSession:
        session = self.get(work_id)
        updated = WorkSession(**{**asdict(session), "state": "closed", "updated_at": datetime.now(timezone.utc).isoformat()})
        self._save(updated)
        return updated

    def _save(self, session: WorkSession) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE work_sessions SET payload = ? WHERE work_id = ?", (json.dumps(asdict(session), sort_keys=True), session.work_id))

    @staticmethod
    def _decode(payload: str) -> WorkSession:
        value = json.loads(payload)
        return WorkSession(
            work_id=value["work_id"], mission_id=value["mission_id"], actor=value["actor"],
            environment=value["environment"], workspace_root=value["workspace_root"],
            state=value["state"], capability_ids=tuple(value.get("capability_ids", ())),
            resources=dict(value.get("resources", {})), created_at=value["created_at"], updated_at=value["updated_at"],
        )


__all__ = ["WorkSession", "WorkSessionStore"]
