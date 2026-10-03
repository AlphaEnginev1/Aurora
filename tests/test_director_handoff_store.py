from pathlib import Path

import pytest

from aurora.orchestration.director_handoff_store import (
    DirectorHandoffError,
    DirectorHandoffRejected,
    DirectorHandoffStore,
)


T0 = "2026-01-01T00:00:00+00:00"


def test_handoff_state_machine_is_durable_and_auditable(tmp_path: Path):
    path = tmp_path / "handoff.db"
    store = DirectorHandoffStore(path)
    requested = store.request(
        "mission-1", "director-A", "director-B", 7,
        handoff_id="handoff-1", now=T0,
    )
    assert requested.state == "REQUESTED"
    assert requested.version == 1

    store = DirectorHandoffStore(path)
    quiescing = store.begin_quiescing("handoff-1", "director-A", 7, now="2026-01-01T00:00:01+00:00")
    assert quiescing.state == "QUIESCING"
    released = store.mark_released("handoff-1", "director-A", 7, now="2026-01-01T00:00:02+00:00")
    assert released.state == "RELEASED"
    assert released.released_at == "2026-01-01T00:00:02+00:00"

    acquired = store.mark_acquired(
        "handoff-1", "director-B", 7, 8, now="2026-01-01T00:00:03+00:00"
    )
    assert acquired.state == "ACQUIRED"
    assert acquired.successor_epoch == 8
    completed = store.complete(
        "handoff-1", "director-B", 7, 8, now="2026-01-01T00:00:04+00:00"
    )
    assert completed.state == "COMPLETED"
    assert store.get_active_for_mission("mission-1") is None

    restarted = DirectorHandoffStore(path)
    assert restarted.get("handoff-1") == completed
    events = restarted.events("handoff-1")
    assert [event.to_state for event in events] == [
        "REQUESTED", "QUIESCING", "RELEASED", "ACQUIRED", "COMPLETED"
    ]
    assert len({event.sequence for event in events}) == 5


def test_rejects_ambiguous_or_invalid_requests(tmp_path: Path):
    store = DirectorHandoffStore(tmp_path / "handoff.db")
    with pytest.raises(DirectorHandoffError):
        store.request("mission", "director-A", "director-A", 1)
    with pytest.raises(DirectorHandoffError):
        store.request("mission", "director-A", "director-B", 0)
    store.request("mission", "director-A", "director-B", 1, handoff_id="h1", now=T0)
    with pytest.raises(DirectorHandoffRejected):
        store.request("mission", "director-A", "director-C", 1, handoff_id="h2", now=T0)


def test_rejects_wrong_actor_epoch_order_and_non_increasing_successor_epoch(tmp_path: Path):
    store = DirectorHandoffStore(tmp_path / "handoff.db")
    store.request("mission", "director-A", "director-B", 4, handoff_id="h", now=T0)
    with pytest.raises(DirectorHandoffRejected):
        store.mark_released("h", "director-A", 4, now="2026-01-01T00:00:01+00:00")
    with pytest.raises(DirectorHandoffRejected):
        store.begin_quiescing("h", "director-X", 4, now="2026-01-01T00:00:01+00:00")
    store.begin_quiescing("h", "director-A", 4, now="2026-01-01T00:00:02+00:00")
    store.mark_released("h", "director-A", 4, now="2026-01-01T00:00:03+00:00")
    with pytest.raises(DirectorHandoffRejected):
        store.mark_acquired("h", "director-X", 4, 5, now="2026-01-01T00:00:04+00:00")
    with pytest.raises(DirectorHandoffRejected):
        store.mark_acquired("h", "director-B", 4, 4, now="2026-01-01T00:00:04+00:00")
    acquired = store.mark_acquired("h", "director-B", 4, 5, now="2026-01-01T00:00:05+00:00")
    assert acquired.state == "ACQUIRED"
    with pytest.raises(DirectorHandoffRejected):
        store.complete("h", "director-B", 4, 6, now="2026-01-01T00:00:06+00:00")


def test_terminal_handoff_keeps_history_and_allows_later_handoff(tmp_path: Path):
    store = DirectorHandoffStore(tmp_path / "handoff.db")
    store.request("mission", "A", "B", 1, handoff_id="first", now=T0)
    store.begin_quiescing("first", "A", 1, now="2026-01-01T00:00:01+00:00")
    store.mark_released("first", "A", 1, now="2026-01-01T00:00:02+00:00")
    store.mark_acquired("first", "B", 1, 2, now="2026-01-01T00:00:03+00:00")
    store.complete("first", "B", 1, 2, now="2026-01-01T00:00:04+00:00")

    second = store.request("mission", "B", "C", 2, handoff_id="second", now="2026-01-01T00:00:05+00:00")
    assert second.state == "REQUESTED"
    assert len(store.events("first")) == 5
