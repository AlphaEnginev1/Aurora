from pathlib import Path

import pytest

from aurora.orchestration.event_bus import DurableEventBus, EventBusError, EventSubscription
from aurora.orchestration.task_store import DurableTaskStore


def test_event_bus_delivery_and_cursor_survive_restart(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    subscription = bus.subscribe("director")

    first = bus.publish(
        mission_id="mission-1",
        event_type="mission_submitted",
        payload={"objective": "test"},
    )
    second = bus.publish(
        mission_id="mission-1",
        event_type="mission_started",
        payload={"worker_id": "worker-a"},
    )

    assert [event.event_id for event in bus.poll(subscription)] == [
        first.event_id,
        second.event_id,
    ]

    bus.ack(subscription, first.event_id)

    restarted = DurableEventBus(DurableTaskStore(tmp_path / "tasks.db"))
    restarted_subscription = restarted.subscribe("director")
    assert [event.event_id for event in restarted.poll(restarted_subscription)] == [
        second.event_id
    ]

    restarted.ack(restarted_subscription, second.event_id)
    assert restarted.poll(restarted_subscription) == ()


def test_event_bus_supports_starting_from_existing_event(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    first = bus.publish(mission_id="m1", event_type="a")
    second = bus.publish(mission_id="m1", event_type="b")

    subscription = bus.subscribe("late-consumer", start_from_event_id=first.event_id)
    assert [event.event_type for event in bus.poll(subscription)] == ["b"]
    bus.ack(subscription, second.event_id)


def test_event_bus_rejects_backwards_ack(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    subscription = bus.subscribe("director")
    first = bus.publish(mission_id="m1", event_type="a")
    second = bus.publish(mission_id="m1", event_type="b")

    bus.ack(subscription, second.event_id)
    with pytest.raises(EventBusError, match="cannot move backwards"):
        bus.ack(subscription, first.event_id)


def test_event_bus_requires_known_consumer(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    with pytest.raises(EventBusError, match="unknown event consumer"):
        bus.poll(EventSubscription("missing"))
