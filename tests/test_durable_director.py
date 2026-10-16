from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

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
    stored_by_id = {item.task.task_id: item for item in stored}
    assert set(stored_by_id) == {"task-census", "task-preflight", "task-file-list"}
    assert stored_by_id["task-preflight"].task.depends_on == ("task-census",)
    assert stored_by_id["task-file-list"].task.depends_on == ("task-preflight",)
    assert stored_by_id["task-file-list"].task.args == (".",)
    submitted_event = next(
        event for event in store.events("mission-explicit-123")
        if event.event_type == "mission_submitted"
    )
    assert submitted_event.payload["provider"] == "nvidia_api"
    assert submitted_event.payload["planning_execution_id"] == "conversation-planning-exec-123"


def test_durable_director_submission_is_idempotent_for_same_mission_id(tmp_path: Path):
    service, store, planner = make_service(tmp_path)

    first = service.submit_mission(objective="test mission", mission_id="mission-idempotent")
    second = service.submit_mission(objective="test mission", mission_id="mission-idempotent")

    assert second.mission_id == first.mission_id
    assert second.plan.tasks == first.plan.tasks
    assert len(planner.calls) == 1
    assert len(store.list_tasks("mission-idempotent")) == 2
    assert len([
        event for event in store.events("mission-idempotent")
        if event.event_type == "mission_submitted"
    ]) == 1


def test_concurrent_identical_durable_submissions_both_replay_one_committed_order(
    tmp_path: Path,
):
    service, store, planner = make_service(tmp_path)
    barrier = Barrier(2)
    original_plan = planner.plan

    def synchronized_plan(**kwargs):
        plan = original_plan(**kwargs)
        barrier.wait(timeout=10)
        return plan

    planner.plan = synchronized_plan

    def submit():
        return service.submit_mission(
            objective="test mission",
            mission_id="mission-concurrent-idempotent",
            correlation_id="corr-concurrent-idempotent",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(lambda _index: submit(), range(2)))

    assert first.mission_id == second.mission_id == "mission-concurrent-idempotent"
    assert first.plan.tasks == second.plan.tasks
    assert len(store.list_tasks("mission-concurrent-idempotent")) == 2
    assert len([
        event for event in store.events("mission-concurrent-idempotent")
        if event.event_type == "mission_submitted"
    ]) == 1
    assert len([
        event for event in store.events("mission-concurrent-idempotent")
        if event.event_type == "enqueued"
    ]) == 2


def test_durable_director_rejects_reuse_of_mission_id_for_different_objective(tmp_path: Path):
    service, store, planner = make_service(tmp_path)
    service.submit_mission(objective="test mission", mission_id="mission-id-conflict")

    with pytest.raises(DurableDirectorError, match="different objective"):
        service.submit_mission(
            objective="a different order",
            mission_id="mission-id-conflict",
        )

    assert len(planner.calls) == 1
    assert len(store.list_tasks("mission-id-conflict")) == 2


def test_durable_director_rejects_reuse_of_mission_id_for_different_explicit_graph(tmp_path: Path):
    service, store, planner = make_service(tmp_path)
    original = (
        DelegatedTask("read-one", "operator", "read first"),
    )
    service.submit_mission(
        objective="explicit idempotency test",
        mission_id="mission-graph-conflict",
        tasks=original,
    )

    with pytest.raises(DurableDirectorError, match="different explicit task graph"):
        service.submit_mission(
            objective="explicit idempotency test",
            mission_id="mission-graph-conflict",
            tasks=(DelegatedTask("read-two", "operator", "read different"),),
        )

    assert planner.calls == []
    assert [item.task.task_id for item in store.list_tasks("mission-graph-conflict")] == ["read-one"]
