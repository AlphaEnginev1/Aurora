"""Durable mission inbox and shared context store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from enum import Enum
import json
import sqlite3

from .task_store import DurableTaskStore, TaskStoreError


class MissionMemoryError(RuntimeError):
    """Raised when durable mission memory cannot be accessed safely."""


class MailboxScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"
    AGENT = "agent"
    TASK = "task"
    MISSION = "mission"


@dataclass(frozen=True)
class MissionMessage:
    message_id: int
    mission_id: str
    source: str
    kind: str
    content: str
    metadata: dict
    created_at: str
    scope: MailboxScope = MailboxScope.MISSION
    project_id: str | None = None
    agent_role: str | None = None
    task_id: str | None = None
    event_id: int | None = None
    correlation_id: str | None = None


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
            columns = {
                row["name"]
                for row in db.execute(
                    "PRAGMA table_info(mission_messages)"
                ).fetchall()
            }
            if "scope" not in columns:
                db.execute(
                    "ALTER TABLE mission_messages ADD COLUMN scope TEXT NOT NULL DEFAULT 'mission'"
                )
            if "project_id" not in columns:
                db.execute(
                    "ALTER TABLE mission_messages ADD COLUMN project_id TEXT"
                )
            if "agent_role" not in columns:
                db.execute(
                    "ALTER TABLE mission_messages ADD COLUMN agent_role TEXT"
                )
            if "task_id" not in columns:
                db.execute(
                    "ALTER TABLE mission_messages ADD COLUMN task_id TEXT"
                )
            if "event_id" not in columns:
                db.execute(
                    "ALTER TABLE mission_messages ADD COLUMN event_id INTEGER"
                )
            if "correlation_id" not in columns:
                db.execute(
                    "ALTER TABLE mission_messages ADD COLUMN correlation_id TEXT"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_mission_messages_project "
                "ON mission_messages (project_id, message_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_mission_messages_agent "
                "ON mission_messages (agent_role, message_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_mission_messages_task "
                "ON mission_messages (task_id, message_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_mission_messages_correlation "
                "ON mission_messages (correlation_id, message_id)"
            )
            # event_id identifies one append-only event globally. Older runtime
            # versions could replay an event before its cursor ack and create
            # duplicate mailbox rows; retain the first projection before enforcing
            # uniqueness going forward.
            db.execute(
                """
                DELETE FROM mission_messages
                WHERE event_id IS NOT NULL
                  AND message_id NOT IN (
                      SELECT MIN(message_id)
                      FROM mission_messages
                      WHERE event_id IS NOT NULL
                      GROUP BY event_id
                  )
                """
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_mission_messages_event_unique "
                "ON mission_messages (event_id) WHERE event_id IS NOT NULL"
            )

    def append(
        self,
        *,
        mission_id: str,
        source: str,
        kind: str,
        content: str,
        metadata: Mapping[str, object] | None = None,
        scope: MailboxScope = MailboxScope.MISSION,
        project_id: str | None = None,
        agent_role: str | None = None,
        task_id: str | None = None,
        event_id: int | None = None,
        correlation_id: str | None = None,
    ) -> MissionMessage:
        if not mission_id.strip():
            raise MissionMemoryError("mission_id is empty")
        if not source.strip():
            raise MissionMemoryError("source is empty")
        if not kind.strip():
            raise MissionMemoryError("kind is empty")
        if not content.strip():
            raise MissionMemoryError("content is empty")
        try:
            scope = MailboxScope(scope)
        except ValueError as exc:
            raise MissionMemoryError(f"invalid mailbox scope: {scope}") from exc
        identifiers = (
            mission_id,
            source,
            kind,
            content,
            project_id,
            agent_role,
            task_id,
            correlation_id,
        )
        if any(value is not None and "\x00" in value for value in identifiers):
            raise MissionMemoryError("mission message contains a null byte")
        if event_id is not None and event_id < 1:
            raise MissionMemoryError("event_id must be positive")

        # A mission mailbox entry inherits the mission's canonical project and
        # correlation identifiers unless the caller supplies an event-specific
        # correlation. Task-linked entries additionally inherit the durable task
        # project and role. This keeps reconstructed and live projections aligned.
        try:
            with self.store._connect() as context_db:
                task_context = None
                if task_id is not None:
                    task_context = context_db.execute(
                        """
                        SELECT project_id, role
                        FROM tasks
                        WHERE mission_id = ? AND task_id = ?
                        """,
                        (mission_id, task_id),
                    ).fetchone()
                submitted = context_db.execute(
                    """
                    SELECT project_id, correlation_id, payload_json
                    FROM task_events
                    WHERE mission_id = ? AND event_type = 'mission_submitted'
                    ORDER BY event_id
                    LIMIT 1
                    """,
                    (mission_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise MissionMemoryError(f"could not resolve mission mailbox context: {exc}") from exc

        submitted_payload = {}
        if submitted is not None:
            try:
                submitted_payload = json.loads(submitted["payload_json"])
            except (TypeError, json.JSONDecodeError):
                submitted_payload = {}
        canonical_project = (
            submitted["project_id"] if submitted is not None else None
        )
        if canonical_project is None and isinstance(submitted_payload, dict):
            value = submitted_payload.get("project_id")
            canonical_project = value if isinstance(value, str) and value.strip() else None
        if (
            canonical_project is not None
            and project_id is not None
            and project_id != canonical_project
        ):
            raise MissionMemoryError(
                "mission mailbox project_id conflicts with the durable submission receipt"
            )
        if task_context is not None and task_context["project_id"] is not None:
            task_project_id = str(task_context["project_id"])
            if canonical_project is not None and task_project_id != canonical_project:
                raise MissionMemoryError(
                    "durable task project_id conflicts with the mission submission receipt"
                )
            if project_id is None:
                project_id = task_project_id
            if agent_role is None:
                agent_role = str(task_context["role"])
        if project_id is None:
            project_id = canonical_project
        if correlation_id is None and submitted is not None:
            correlation_id = submitted["correlation_id"]
            if correlation_id is None and isinstance(submitted_payload, dict):
                value = submitted_payload.get("correlation_id")
                correlation_id = (
                    value if isinstance(value, str) and value.strip() else None
                )

        if scope is MailboxScope.PROJECT and not (project_id and project_id.strip()):
            raise MissionMemoryError("project scope requires project_id")
        if scope is MailboxScope.AGENT and not (agent_role and agent_role.strip()):
            raise MissionMemoryError("agent scope requires agent_role")
        if scope is MailboxScope.TASK and not (task_id and task_id.strip()):
            raise MissionMemoryError("task scope requires task_id")

        now = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(dict(metadata or {}), sort_keys=True)
        try:
            with self.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if event_id is not None:
                    existing = db.execute(
                        """
                        SELECT message_id, mission_id, source, kind, content,
                               metadata_json, created_at, scope, project_id,
                               agent_role, task_id, event_id, correlation_id
                        FROM mission_messages
                        WHERE event_id = ?
                        """,
                        (event_id,),
                    ).fetchone()
                    if existing is not None:
                        same_projection = (
                            existing["mission_id"] == mission_id
                            and existing["source"] == source
                            and existing["kind"] == kind
                            and existing["content"] == content
                            and existing["metadata_json"] == payload
                            and existing["scope"] == scope.value
                            and existing["project_id"] == project_id
                            and existing["agent_role"] == agent_role
                            and existing["task_id"] == task_id
                            and existing["correlation_id"] == correlation_id
                        )
                        if not same_projection:
                            db.rollback()
                            raise MissionMemoryError(
                                f"event_id {event_id} is already bound to a different mailbox projection"
                            )
                        db.rollback()
                        return MissionMessage(
                            message_id=int(existing["message_id"]),
                            mission_id=str(existing["mission_id"]),
                            source=str(existing["source"]),
                            kind=str(existing["kind"]),
                            content=str(existing["content"]),
                            metadata=json.loads(existing["metadata_json"]),
                            created_at=str(existing["created_at"]),
                            scope=MailboxScope(existing["scope"]),
                            project_id=existing["project_id"],
                            agent_role=existing["agent_role"],
                            task_id=existing["task_id"],
                            event_id=int(existing["event_id"]),
                            correlation_id=existing["correlation_id"],
                        )
                cursor = db.execute(
                    """
                    INSERT INTO mission_messages (
                        mission_id, source, kind, content, metadata_json, created_at,
                        scope, project_id, agent_role, task_id, event_id, correlation_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mission_id,
                        source,
                        kind,
                        content,
                        payload,
                        now,
                        scope.value,
                        project_id,
                        agent_role,
                        task_id,
                        event_id,
                        correlation_id,
                    ),
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
            scope=scope,
            project_id=project_id,
            agent_role=agent_role,
            task_id=task_id,
            event_id=event_id,
            correlation_id=correlation_id,
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
                           metadata_json, created_at, scope, project_id,
                           agent_role, task_id, event_id, correlation_id
                    FROM mission_messages
                    WHERE mission_id = ?
                    ORDER BY message_id DESC
                    LIMIT ?
                    """,
                    (mission_id, limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MissionMemoryError(f"could not read mission messages: {exc}") from exc

        messages = [
            MissionMessage(
                message_id=row["message_id"],
                mission_id=row["mission_id"],
                source=row["source"],
                kind=row["kind"],
                content=row["content"],
                metadata=json.loads(row["metadata_json"]),
                created_at=row["created_at"],
                scope=MailboxScope(row["scope"]),
                project_id=row["project_id"],
                agent_role=row["agent_role"],
                task_id=row["task_id"],
                event_id=row["event_id"],
                correlation_id=row["correlation_id"],
            )
            for row in rows
        ]
        return tuple(reversed(messages))

    def list_global(self, *, limit: int = 100) -> tuple[MissionMessage, ...]:
        """Return the global cross-scope mailbox projection."""
        return self._list_view(limit=limit)

    def list_project(self, project_id: str, *, limit: int = 100) -> tuple[MissionMessage, ...]:
        if not project_id.strip():
            raise MissionMemoryError("project_id is empty")
        return self._list_view(project_id=project_id, limit=limit)

    def list_agent(self, agent_role: str, *, limit: int = 100) -> tuple[MissionMessage, ...]:
        if not agent_role.strip():
            raise MissionMemoryError("agent_role is empty")
        return self._list_view(agent_role=agent_role, limit=limit)

    def list_task(self, task_id: str, *, limit: int = 100) -> tuple[MissionMessage, ...]:
        if not task_id.strip():
            raise MissionMemoryError("task_id is empty")
        return self._list_view(task_id=task_id, limit=limit)

    def list_correlation(
        self, correlation_id: str, *, limit: int = 100
    ) -> tuple[MissionMessage, ...]:
        if not correlation_id.strip():
            raise MissionMemoryError("correlation_id is empty")
        return self._list_view(correlation_id=correlation_id, limit=limit)

    def _list_view(
        self,
        *,
        project_id: str | None = None,
        agent_role: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MissionMessage, ...]:
        if limit < 1:
            raise MissionMemoryError("limit must be at least 1")
        filters = ["1=1"]
        params: list[object] = []
        for column, value in (
            ("project_id", project_id),
            ("agent_role", agent_role),
            ("task_id", task_id),
            ("correlation_id", correlation_id),
        ):
            if value is not None:
                filters.append(f"{column} = ?")
                params.append(value)
        params.append(limit)
        try:
            with self.store._connect() as db:
                rows = db.execute(
                    f"""
                    SELECT message_id, mission_id, source, kind, content,
                           metadata_json, created_at, scope, project_id,
                           agent_role, task_id, event_id, correlation_id
                    FROM mission_messages
                    WHERE {" AND ".join(filters)}
                    ORDER BY message_id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MissionMemoryError(f"could not read mailbox view: {exc}") from exc
        return tuple(
            reversed(
                tuple(
                    MissionMessage(
                        message_id=row["message_id"],
                        mission_id=row["mission_id"],
                        source=row["source"],
                        kind=row["kind"],
                        content=row["content"],
                        metadata=json.loads(row["metadata_json"]),
                        created_at=row["created_at"],
                        scope=MailboxScope(row["scope"]),
                        project_id=row["project_id"],
                        agent_role=row["agent_role"],
                        task_id=row["task_id"],
                        event_id=row["event_id"],
                        correlation_id=row["correlation_id"],
                    )
                    for row in rows
                )
            )
        )

    def task_result_task_ids(self, mission_id: str) -> frozenset[str]:
        """Return task IDs with a task_result mailbox projection for this mission."""
        if not mission_id.strip():
            raise MissionMemoryError("mission_id is empty")
        try:
            with self.store._connect() as db:
                rows = db.execute(
                    """
                    SELECT metadata_json, task_id
                    FROM mission_messages
                    WHERE mission_id = ? AND kind = 'task_result'
                    """,
                    (mission_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MissionMemoryError(f"could not read task result projections: {exc}") from exc
        result: set[str] = set()
        for row in rows:
            task_id = row["task_id"]
            if not task_id:
                task_id = json.loads(row["metadata_json"]).get("task_id")
            if isinstance(task_id, str) and task_id.strip():
                result.add(task_id)
        return frozenset(result)

    def first_of_kind(self, mission_id: str, kind: str) -> MissionMessage | None:
        if not kind.strip():
            raise MissionMemoryError("kind is empty")
        try:
            with self.store._connect() as db:
                row = db.execute(
                    """
                    SELECT message_id, mission_id, source, kind, content,
                           metadata_json, created_at, scope, project_id,
                           agent_role, task_id, event_id, correlation_id
                    FROM mission_messages
                    WHERE mission_id = ? AND kind = ?
                    ORDER BY message_id
                    LIMIT 1
                    """,
                    (mission_id, kind),
                ).fetchone()
        except sqlite3.Error as exc:
            raise MissionMemoryError(f"could not read mission message: {exc}") from exc
        if row is None:
            return None
        return MissionMessage(
            message_id=row["message_id"],
            mission_id=row["mission_id"],
            source=row["source"],
            kind=row["kind"],
            content=row["content"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            scope=MailboxScope(row["scope"]),
            project_id=row["project_id"],
            agent_role=row["agent_role"],
            task_id=row["task_id"],
            event_id=row["event_id"],
            correlation_id=row["correlation_id"],
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


__all__ = ["MailboxScope", "MissionMemory", "MissionMemoryError", "MissionMessage"]
