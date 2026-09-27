"""Durable Event Bus backed by Aurora's SQLite event journal.

The bus provides durable consumer cursors and at-least-once delivery. The
journal remains the source of truth; consumers acknowledge events only after
handling them successfully.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .task_store import DurableTaskStore, TaskEvent, TaskStoreError


class EventBusError(RuntimeError):
    """Raised when durable event delivery cannot proceed safely."""


@dataclass(frozen=True)
class EventSubscription:
    consumer_id: str


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
    ) -> TaskEvent:
        try:
            return self.store.append_event(
                mission_id=mission_id,
                task_id=task_id,
                event_type=event_type,
                payload=dict(payload or {}),
            )
        except TaskStoreError as exc:
            raise EventBusError(str(exc)) from exc

    def subscribe(
        self,
        consumer_id: str,
        *,
        start_from_event_id: int = 0,
    ) -> EventSubscription:
        if not consumer_id.strip():
            raise EventBusError("consumer_id is empty")
        if start_from_event_id < 0:
            raise EventBusError("start_from_event_id cannot be negative")
        try:
            self.store.ensure_event_consumer(
                consumer_id,
                initial_event_id=start_from_event_id,
            )
        except TaskStoreError as exc:
            raise EventBusError(str(exc)) from exc
        return EventSubscription(consumer_id=consumer_id)

    def poll(
        self,
        subscription: EventSubscription,
        *,
        limit: int = 100,
    ) -> tuple[TaskEvent, ...]:
        if limit < 1:
            raise EventBusError("limit must be at least 1")
        try:
            cursor = self.store.get_event_cursor(subscription.consumer_id)
            return self.store.events_since(after_event_id=cursor, limit=limit)
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
            self.store.ack_event(subscription.consumer_id, event_id)
        except TaskStoreError as exc:
            raise EventBusError(str(exc)) from exc


__all__ = ["DurableEventBus", "EventBusError", "EventSubscription"]
