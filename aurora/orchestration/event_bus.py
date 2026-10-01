"""Durable Event Bus backed by Aurora's SQLite event journal.

The bus provides durable consumer cursors and at-least-once delivery. The
journal remains the source of truth; consumers acknowledge events only after
handling them successfully.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping
import json

from .task_store import DurableTaskStore, TaskEvent, TaskStoreError


class EventBusError(RuntimeError):
    """Raised when durable event delivery cannot proceed safely."""


@dataclass(frozen=True)
class EventSubscription:
    consumer_id: str
    mission_id: str | None = None
    project_id: str | None = None
    agent_role: str | None = None
    correlation_id: str | None = None
    cursor_id: str | None = None


class DurableEventBus:
    """Publish and consume durable events with restart-safe cursors."""

    def __init__(self, store: DurableTaskStore) -> None:
        self.store = store

    def publish(
        self,
        *,
        mission_id: str,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        task_id: str | None = None,
        project_id: str | None = None,
        agent_role: str | None = None,
        correlation_id: str | None = None,
    ) -> TaskEvent:
        try:
            return self.store.append_event(
                mission_id=mission_id,
                task_id=task_id,
                event_type=event_type,
                payload=dict(payload or {}),
                project_id=project_id,
                agent_role=agent_role,
                correlation_id=correlation_id,
            )
        except TaskStoreError as exc:
            raise EventBusError(str(exc)) from exc

    def subscribe(
        self,
        consumer_id: str,
        *,
        start_from_event_id: int = 0,
        mission_id: str | None = None,
        project_id: str | None = None,
        agent_role: str | None = None,
        correlation_id: str | None = None,
    ) -> EventSubscription:
        if not consumer_id.strip():
            raise EventBusError("consumer_id is empty")
        if start_from_event_id < 0:
            raise EventBusError("start_from_event_id cannot be negative")
        cursor_id = _cursor_id(
            consumer_id,
            mission_id=mission_id,
            project_id=project_id,
            agent_role=agent_role,
            correlation_id=correlation_id,
        )
        try:
            self.store.ensure_event_consumer(
                cursor_id,
                initial_event_id=start_from_event_id,
            )
        except TaskStoreError as exc:
            raise EventBusError(str(exc)) from exc
        return EventSubscription(
            consumer_id=consumer_id,
            mission_id=mission_id,
            project_id=project_id,
            agent_role=agent_role,
            correlation_id=correlation_id,
            cursor_id=cursor_id,
        )

    def poll(
        self,
        subscription: EventSubscription,
        *,
        limit: int = 100,
    ) -> tuple[TaskEvent, ...]:
        if limit < 1:
            raise EventBusError("limit must be at least 1")
        try:
            cursor = self.store.get_event_cursor(
                subscription.cursor_id or subscription.consumer_id
            )
            return self.store.events_since(
                after_event_id=cursor,
                limit=limit,
                mission_id=subscription.mission_id,
                project_id=subscription.project_id,
                agent_role=subscription.agent_role,
                correlation_id=subscription.correlation_id,
            )
        except TaskStoreError as exc:
            raise EventBusError(str(exc)) from exc

    def ack(
        self,
        subscription: EventSubscription,
        event_id: int,
    ) -> None:
        if event_id < 1:
            raise EventBusError("event_id must be positive")
        try:
            filtered = any(
                value is not None
                for value in (
                    subscription.mission_id,
                    subscription.project_id,
                    subscription.agent_role,
                    subscription.correlation_id,
                )
            )
            self.store.ack_event(
                subscription.cursor_id or subscription.consumer_id,
                event_id,
                allow_gaps=filtered,
            )
        except TaskStoreError as exc:
            raise EventBusError(str(exc)) from exc


__all__ = ["DurableEventBus", "EventBusError", "EventSubscription"]


def _cursor_id(
    consumer_id: str,
    *,
    mission_id: str | None,
    project_id: str | None,
    agent_role: str | None,
    correlation_id: str | None,
) -> str:
    filters = {
        "mission_id": mission_id,
        "project_id": project_id,
        "agent_role": agent_role,
        "correlation_id": correlation_id,
    }
    if not any(value is not None for value in filters.values()):
        return consumer_id
    digest = sha256(
        json.dumps(filters, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"{consumer_id}:view:{digest}"
