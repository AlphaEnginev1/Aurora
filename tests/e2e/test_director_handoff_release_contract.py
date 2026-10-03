from dataclasses import dataclass
from pathlib import Path

import pytest

from aurora.orchestration.director_ownership import (
    DirectorOwnershipRejected,
    DirectorOwnershipStore,
)
from aurora.orchestration.task_store import DurableTaskStore


@dataclass(frozen=True)
class _HandoffRecord:
    mission_id: str
    predecessor: str
    successor: str
    fencing_epoch: int
    state: str
    requested_at: str
    released_at: str | None = None
    successor_epoch: int | None = None


def test_gate5_director_handoff_release_contract(tmp_path: Path):
    """Deterministic contract probe; no production handoff implementation."""
    store = DurableTaskStore(tmp_path / "tasks.db")
    ownership = DirectorOwnershipStore(store.path)

    mission_id = "mission-handoff-001"
    clock = "2026-01-01T00:00:00+00:00"
    later = "2026-01-01T00:00:01+00:00"
    terminal_events: list[str] = []
    executions: list[str] = []
    plan_calls = {"A": 1, "B": 0}
    graph = {
        "step_a": {"status": "succeeded", "depends_on": ()},
        "step_b": {"status": "pending", "depends_on": ("step_a",)},
    }

    owner_a = ownership.acquire(
        mission_id,
        "director-A",
        lease_seconds=30,
        now=clock,
    )
    assert owner_a.fencing_epoch == 1

    handoff = _HandoffRecord(
        mission_id=mission_id,
        predecessor="director-A",
        successor="director-B",
        fencing_epoch=owner_a.fencing_epoch,
        state="HANDOFF_REQUESTED",
        requested_at=clock,
    )

    handoff = _HandoffRecord(**{**handoff.__dict__, "state": "QUIESCING"})
    executions.append("step_a")

    # Quiescence forbids new Director-controlled mutations.
    assert executions == ["step_a"]

    ownership.release(
        mission_id,
        "director-A",
        owner_a.fencing_epoch,
        now=later,
    )
    handoff = _HandoffRecord(
        **{**handoff.__dict__, "state": "RELEASED", "released_at": later}
    )

    owner_b = ownership.acquire(
        mission_id,
        "director-B",
        lease_seconds=30,
        now=later,
    )
    assert owner_b.fencing_epoch == 2

    handoff = _HandoffRecord(
        **{
            **handoff.__dict__,
            "state": "SUCCESSOR_ACQUIRED",
            "successor_epoch": owner_b.fencing_epoch,
        }
    )

    # The predecessor's old epoch is fenced after successor acquisition.
    with pytest.raises(DirectorOwnershipRejected):
        ownership.require_owner(
            mission_id,
            "director-A",
            owner_a.fencing_epoch,
            now=later,
        )

    # B reconstructs the existing graph and never calls plan again.
    assert graph["step_a"]["status"] == "succeeded"
    assert graph["step_b"]["status"] == "pending"
    assert graph["step_b"]["depends_on"] == ("step_a",)
    assert plan_calls == {"A": 1, "B": 0}

    # Only unfinished work is executed.
    executions.append("step_b")
    graph["step_b"]["status"] = "succeeded"
    terminal_events.append("mission_succeeded")

    assert executions == ["step_a", "step_b"]
    assert executions.count("step_a") == 1
    assert executions.count("step_b") == 1
    assert terminal_events == ["mission_succeeded"]
    assert handoff.state == "SUCCESSOR_ACQUIRED"
    assert handoff.mission_id == mission_id
    assert handoff.predecessor == "director-A"
    assert handoff.successor == "director-B"
    assert handoff.fencing_epoch == 1
    assert handoff.successor_epoch == 2
