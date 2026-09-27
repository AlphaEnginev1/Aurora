"""Durable mission inbox and shared context store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
import json
import sqlite3

from .task_store import DurableTaskStore, TaskStoreError


class MissionMemoryError(RuntimeError):
    """Raised when durable mission memory cannot be accessed safely."""


@dataclass(frozen=True)
class MissionMessage:
    message_id: int
    mission_id: str
    source: str
    kind: str
    content: str
    metadata: dict
    created_at: str


class MissionMemory:
    """Shared durable inbox/context layered on the same SQLite database."""

    def __init__(self, store: DurableTaskStore) -> None:
        self.store = store
        with self.store._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_mission_messages "
                "ON mission_messages (mission_id, message_id)"
            )

    def append(
        self,
        *,
        mission_id: str,
        source: str,
        kind: str,
        content: str,
        metadata: Mapping[str, object] | None = None,
    ) -> MissionMessage:
        if not mission_id.strip():
            raise MissionMemoryError("mission_id is empty")
        if not source.strip():
            raise MissionMemoryError("source is empty")
        if not kind.strip():
            raise MissionMemoryError("kind is empty")
        if not content.strip():
            raise MissionMemoryError("content is empty")
        if "\x00" in mission_id or "\x00" in source or "\x00" in kind or "\x00" in content:
            raise MissionMemoryError("mission message contains a null byte")

        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(dict(metadata or {}), sort_keys=True)
        try:
            with self.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    """
                    INSERT INTO mission_messages (
                        mission_id, source, kind, content, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (mission_id, source, kind, content, payload, now),
                )
                message_id = int(cursor.lastrowid)
                db.commit()
        except sqlite3.Error as exc:
            raise MissionMemoryError(f"could not append mission message: {exc}") from exc

        return MissionMessage(
            message_id=message_id,
            mission_id=mission_id,
            source=source,
            kind=kind,
            content=content,
            metadata=dict(metadata or {}),
            created_at=now,
        )

    def list(
        self,
        mission_id: str,
        *,
        limit: int = 100,
    ) -> tuple[MissionMessage, ...]:
        if limit < 1:
            raise MissionMemoryError("limit must be at least 1")
        try:
            with self.store._connect() as db:
                rows = db.execute(
                    """
                    SELECT message_id, mission_id, source, kind, content,
                           metadata_json, created_at
                    FROM mission_messages
                    WHERE mission_id = ?
                    ORDER BY message_id
                    LIMIT ?
                    """,
                    (mission_id, limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MissionMemoryError(f"could not read mission messages: {exc}") from exc

        return tuple(
            MissionMessage(
                message_id=row["message_id"],
                mission_id=row["mission_id"],
                source=row["source"],
                kind=row["kind"],
                content=row["content"],
                metadata=json.loads(row["metadata_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        )

    def context(
        self,
        mission_id: str,
        *,
        limit: int = 40,
    ) -> list[dict[str, str]]:
        messages = self.list(mission_id, limit=limit)
        return [
            {
                "role": "user",
                "content": (
                    f"[{message.kind}] source={message.source}\n"
                    f"{message.content}"
                ),
            }
            for message in messages
        ]


__all__ = ["MissionMemory", "MissionMemoryError", "MissionMessage"]
