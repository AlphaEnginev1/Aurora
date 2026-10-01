"""Unified Aurora mailbox facade backed by the durable event journal.

MissionMemory remains as a compatibility-facing context API, but it no longer
owns a second journal. Mailbox messages are stored as ordinary durable events
with contextual metadata and can be queried through global/project/agent views.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Mapping

from .task_store import DurableTaskStore, TaskEvent, TaskStoreError


MAILBOX_EVENT_TYPE = "mailbox.message"


class MissionMemoryError(RuntimeError):
    """Raised when durable mailbox/context state cannot be accessed safely."""


@dataclass(frozen=True)
class MissionMessage:
    message_id: int
    mission_id: str
    source: str
    kind: str
    content: str
    metadata: dict
    created_at: str
    project_id: str | None = None
    agent_role: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None


class MissionMemory:
    """Compatibility facade over Aurora's single durable event journal."""

    def __init__(self, store: DurableTaskStore) -> None:
        self.store = store
        self._migrate_legacy_messages()

    def append(
        self,
        *,
        mission_id: str,
        source: str,
        kind: str,
        content: str,
        metadata: Mapping[str, object] | None = None,
        project_id: str | None = None,
        agent_role: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> MissionMessage:
        for name, value in (
            ("mission_id", mission_id),
            ("source", source),
            ("kind", kind),
            ("content", content),
        ):
            if not value.strip():
                raise MissionMemoryError(f"{name} is empty")
            if "\x00" in value:
                raise MissionMemoryError(f"{name} contains a null byte")

        payload = {
            "source": source,
            "kind": kind,
            "content": content,
            "metadata": dict(metadata or {}),
        }
        try:
            event = self.store.append_event(
                mission_id=mission_id,
                task_id=task_id,
                event_type=MAILBOX_EVENT_TYPE,
                payload=payload,
                project_id=_clean_optional(project_id, "project_id"),
                agent_role=_clean_optional(agent_role, "agent_role"),
                correlation_id=_clean_optional(correlation_id, "correlation_id"),
                causation_id=_clean_optional(causation_id, "causation_id"),
            )
        except TaskStoreError as exc:
            raise MissionMemoryError(str(exc)) from exc
        return self._event_to_message(event)

    def list(
        self,
        mission_id: str,
        *,
        limit: int = 100,
    ) -> tuple[MissionMessage, ...]:
        if not mission_id.strip():
            raise MissionMemoryError("mission_id is empty")
        if limit < 1:
            raise MissionMemoryError("limit must be at least 1")
        try:
            events = self.store.events_view(
                mission_id=mission_id,
                event_type=MAILBOX_EVENT_TYPE,
                limit=limit,
            )
        except TaskStoreError as exc:
            raise MissionMemoryError(str(exc)) from exc
        return tuple(self._event_to_message(event) for event in events)

    def list_global(self, *, limit: int = 100) -> tuple[MissionMessage, ...]:
        return self._view(limit=limit)

    def list_project(
        self,
        project_id: str,
        *,
        limit: int = 100,
    ) -> tuple[MissionMessage, ...]:
        return self._view(
            project_id=_clean_required(project_id, "project_id"),
            limit=limit,
        )

    def list_agent(
        self,
        agent_role: str,
        *,
        limit: int = 100,
    ) -> tuple[MissionMessage, ...]:
        return self._view(
            agent_role=_clean_required(agent_role, "agent_role"),
            limit=limit,
        )

    def list_task(
        self,
        task_id: str,
        *,
        limit: int = 100,
    ) -> tuple[MissionMessage, ...]:
        return self._view(
            task_id=_clean_required(task_id, "task_id"),
            limit=limit,
        )

    def list_correlation(
        self,
        correlation_id: str,
        *,
        limit: int = 100,
    ) -> tuple[MissionMessage, ...]:
        return self._view(
            correlation_id=_clean_required(correlation_id, "correlation_id"),
            limit=limit,
        )

    def first_of_kind(self, mission_id: str, kind: str) -> MissionMessage | None:
        if not kind.strip():
            raise MissionMemoryError("kind is empty")
        for message in self.list(mission_id, limit=1000):
            if message.kind == kind:
                return message
        return None

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

    def _view(
        self,
        *,
        mission_id: str | None = None,
        project_id: str | None = None,
        agent_role: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MissionMessage, ...]:
        if limit < 1:
            raise MissionMemoryError("limit must be at least 1")
        try:
            events = self.store.events_view(
                mission_id=mission_id,
                project_id=project_id,
                agent_role=agent_role,
                task_id=task_id,
                correlation_id=correlation_id,
                event_type=MAILBOX_EVENT_TYPE,
                limit=limit,
            )
        except TaskStoreError as exc:
            raise MissionMemoryError(str(exc)) from exc
        return tuple(self._event_to_message(event) for event in events)

    def _migrate_legacy_messages(self) -> None:
        """Move the pre-unification mission_messages table into task_events once."""
        try:
            with self.store._connect() as db:
                exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mission_messages'"
                ).fetchone()
                if exists is None:
                    return
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute(
                    """
                    SELECT message_id, mission_id, source, kind, content,
                           metadata_json, created_at
                    FROM mission_messages
                    ORDER BY message_id
                    """
                ).fetchall()
                for row in rows:
                    payload = {
                        "source": str(row["source"]),
                        "kind": str(row["kind"]),
                        "content": str(row["content"]),
                        "metadata": json.loads(row["metadata_json"]),
                        "legacy_message_id": int(row["message_id"]),
                    }
                    db.execute(
                        """
                        INSERT INTO task_events (
                            mission_id, task_id, event_type, payload_json, created_at,
                            project_id, agent_role, correlation_id, causation_id
                        ) VALUES (?, NULL, ?, ?, ?, NULL, NULL, NULL, NULL)
                        """,
                        (
                            str(row["mission_id"]),
                            MAILBOX_EVENT_TYPE,
                            json.dumps(payload, sort_keys=True),
                            str(row["created_at"]) or datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                db.execute("DROP TABLE mission_messages")
                db.commit()
        except sqlite3.Error as exc:
            raise MissionMemoryError(f"could not migrate legacy mission memory: {exc}") from exc

    @staticmethod
    def _event_to_message(event: TaskEvent) -> MissionMessage:
        payload = event.payload
        try:
            source = str(payload["source"])
            kind = str(payload["kind"])
            content = str(payload["content"])
        except (KeyError, TypeError) as exc:
            raise MissionMemoryError(
                f"invalid mailbox event payload: event_id={event.event_id}"
            ) from exc
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise MissionMemoryError(
                f"invalid mailbox event metadata: event_id={event.event_id}"
            )
        return MissionMessage(
            message_id=event.event_id,
            mission_id=event.mission_id,
            source=source,
            kind=kind,
            content=content,
            metadata=dict(metadata),
            created_at=event.created_at,
            project_id=event.project_id,
            agent_role=event.agent_role,
            task_id=event.task_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
        )


def _clean_optional(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if "\x00" in cleaned:
        raise MissionMemoryError(f"{name} contains a null byte")
    return cleaned


def _clean_required(value: str, name: str) -> str:
    cleaned = _clean_optional(value, name)
    if cleaned is None:
        raise MissionMemoryError(f"{name} is empty")
    return cleaned


__all__ = ["MAILBOX_EVENT_TYPE", "MissionMemory", "MissionMemoryError", "MissionMessage"]
