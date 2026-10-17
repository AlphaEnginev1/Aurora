from types import SimpleNamespace

import pytest

from aurora.autonomy.mission_manager import (
    AutonomousMissionManager,
    MissionManagerError,
)


class FakeRuntime:
    def __init__(self, cycles_run: int = 3):
        self.cycles_run = cycles_run
        self.submissions = []
        self.max_cycles_seen = None

    def submit_mission(self, *, objective, context=None):
        self.submissions.append((objective, context))
        return SimpleNamespace(mission_id="mission-cycle-count")

    def run_until_terminal(self, submission, *, max_cycles):
        self.max_cycles_seen = max_cycles
        return SimpleNamespace(
            submission=submission,
            status="succeeded",
            cycles_run=self.cycles_run,
        )


def test_mission_manager_reports_actual_cycles_not_cycle_budget():
    runtime = FakeRuntime(cycles_run=3)
    manager = AutonomousMissionManager(runtime, max_cycles=10)

    result = manager.run("  verify cycle count  ", context=[{"role": "user", "content": "context"}])

    assert result.mission_id == "mission-cycle-count"
    assert result.status == "succeeded"
    assert result.cycles == 3
    assert result.cycles != runtime.max_cycles_seen
    assert runtime.max_cycles_seen == 10
    assert runtime.submissions == [
        ("verify cycle count", [{"role": "user", "content": "context"}])
    ]


def test_mission_manager_rejects_invalid_cycle_budget():
    with pytest.raises(MissionManagerError, match="max_cycles"):
        AutonomousMissionManager(FakeRuntime(), max_cycles=0)


def test_mission_manager_rejects_empty_objective():
    with pytest.raises(MissionManagerError, match="objective"):
        AutonomousMissionManager(FakeRuntime()).run("   ")
