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


def test_durable_director_persists_explicit_validated_task_graph_and_planning_evidence(tmp_path: Path):
    service, store, planner = make_service(tmp_path)
    tasks = (
        DelegatedTask("task-census", "operator", "Read-only Workbench operation census",
                      capability="workbench:census"),
        DelegatedTask("task-preflight", "operator", "Read-only Workbench operation preflight",
                      capability="workbench:preflight", depends_on=("task-census",)),
        DelegatedTask("task-file-list", "operator", "Read-only Workbench operation file:list",
                      capability="workbench:file:list", args=(".",),
                      depends_on=("task-preflight",)),
    )

    submission = service.submit_mission(
        objective="Validación Durable de Aurora",
        mission_id="mission-explicit-123",
        tasks=tasks,
        planning_model="nvidia/test-model",
        planning_provider="nvidia_api",
        planning_execution_id="conversation-planning-exec-123",
    )

    stored = store.list_tasks("mission-explicit-123")
    assert planner.calls == []
    assert submission.mission_id == "mission-explicit-123"
    assert submission.plan.model == "nvidia/test-model"
    assert submission.plan.provider == "nvidia_api"
    assert submission.plan.execution_id == "conversation-planning-exec-123"
    assert [item.task.task_id for item in stored] == [
        "task-census", "task-preflight", "task-file-list"
    ]
    assert stored[1].task.depends_on == ("task-census",)
    assert stored[2].task.depends_on == ("task-preflight",)
    assert stored[2].task.args == (".",)
