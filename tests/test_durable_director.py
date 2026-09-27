from pathlib import Path

import pytest

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan
from aurora.orchestration.durable_director import (
    DurableDirectorError,
    DurableDirectorService,
)
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine
from aurora.orchestration.task_store import DurableTaskStore


class FakePlanner:
    def __init__(self, plan):
        self.plan_value = plan
        self.calls = []

    def plan(self, **kwargs):
        self.calls.append(kwargs)
        return self.plan_value


class FakeOrchestration:
    def run_task(self, **kwargs):
        return object()

    def run_capability_task(self, **kwargs):
        raise AssertionError("not expected")


def make_service(tmp_path: Path):
    plan = DirectorPlan(
        objective="test mission",
        tasks=(
            DelegatedTask("a", "researcher", "collect"),
            DelegatedTask("b", "reviewer", "review", depends_on=("a",)),
        ),
        model="fake/model",
    )
    planner = FakePlanner(plan)
    engine = TaskEngine(FakeOrchestration(), tmp_path / "workspace")
    store = DurableTaskStore(tmp_path / "tasks.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=engine,
        store=store,
    )
    return service, store, planner


def test_durable_director_persists_validated_plan(tmp_path: Path):
    service, store, planner = make_service(tmp_path)

    submission = service.submit_mission(
        objective="test mission",
        mission_id="mission-1",
    )

    assert submission.mission_id == "mission-1"
    assert submission.plan.objective == "test mission"
    assert [task.task.task_id for task in store.list_tasks("mission-1")] == ["a", "b"]
    assert planner.calls[0]["objective"] == "test mission"


def test_durable_director_generates_mission_id(tmp_path: Path):
    service, store, _ = make_service(tmp_path)

    submission = service.submit_mission(objective="test mission")

    assert submission.mission_id.startswith("mission-")
    assert len(store.list_tasks(submission.mission_id)) == 2


def test_durable_director_refuses_invalid_plan(tmp_path: Path):
    plan = DirectorPlan(
        objective="bad",
        tasks=(DelegatedTask("a", "agent", "work", depends_on=("missing",)),),
        model="fake/model",
    )
    planner = FakePlanner(plan)
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(FakeOrchestration(), tmp_path / "workspace"),
        store=DurableTaskStore(tmp_path / "tasks.db"),
    )

    with pytest.raises(DurableDirectorError, match="could not be durably submitted"):
        service.submit_mission(objective="bad", mission_id="mission-bad")
