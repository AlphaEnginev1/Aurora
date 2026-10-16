from pathlib import Path

import pytest

from aurora.orchestration.director import DirectorPlan
from aurora.orchestration.durable_director import DurableDirectorError, DurableDirectorService
from aurora.inference.models import InferenceResponse
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine
from aurora.orchestration.task_store import DurableTaskStore
from aurora.projects.registry import ProjectRegistry, ProjectSpec, ProjectStatus


class _Planner:
    def __init__(self):
        self.calls = []
        self.project_registry = ProjectRegistry({
            "project-a": ProjectSpec(
                project_id="project-a",
                name="Project A",
                objective="Keep Project A isolated",
                status=ProjectStatus.ACTIVE,
                repository="org/project-a",
                current_state="canonical state A",
            ),
            "project-b": ProjectSpec(
                project_id="project-b",
                name="Project B",
                objective="Keep Project B isolated",
                status=ProjectStatus.ACTIVE,
                repository="org/project-b",
                current_state="canonical state B",
            ),
        })

    def plan(self, *, objective, context=None, model=None):
        self.calls.append({"objective": objective, "context": context, "model": model})
        return DirectorPlan(
            objective=objective,
            tasks=(DelegatedTask("task-a", "researcher", "Inspect project context"),),
            model="test/model",
        )


class _Orchestration:
    def run_task(self, **kwargs):
        raise AssertionError("submission must not execute a worker")

    def run_capability_task(self, **kwargs):
        raise AssertionError("submission must not execute a capability")


def test_project_id_is_resolved_persisted_and_propagated_to_durable_tasks(tmp_path: Path):
    planner = _Planner()
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace"),
        store=store,
    )

    submission = service.submit_mission(
        objective="Audit Project A",
        mission_id="mission-project-a",
        correlation_id="corr-project-a",
        project_id="project-a",
    )

    stored = store.list_tasks(submission.mission_id)
    assert len(stored) == 1
    assert stored[0].task.project_id == "project-a"
    assert planner.calls[0]["context"][-1]["content"].find('"project_id": "project-a"') >= 0
    assert "canonical state A" in planner.calls[0]["context"][-1]["content"]
    event = next(
        item for item in store.events(submission.mission_id)
        if item.event_type == "mission_submitted"
    )
    assert event.project_id == "project-a"
    assert event.payload["project_id"] == "project-a"
    assert event.payload["task_graph"][0]["project_id"] == "project-a"


def test_project_scoped_mission_rejects_unknown_project(tmp_path: Path):
    planner = _Planner()
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace"),
        store=DurableTaskStore(tmp_path / "missions.db"),
    )
    with pytest.raises(DurableDirectorError, match="unknown project_id"):
        service.submit_mission(objective="Bad project", project_id="not-registered")


def test_mission_idempotency_cannot_switch_project(tmp_path: Path):
    planner = _Planner()
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace"),
        store=store,
    )
    service.submit_mission(
        objective="Audit project",
        mission_id="mission-project-bound",
        project_id="project-a",
    )
    with pytest.raises(DurableDirectorError, match="different project_id"):
        service.submit_mission(
            objective="Audit project",
            mission_id="mission-project-bound",
            project_id="project-b",
        )
    assert len(store.list_tasks("mission-project-bound")) == 1


def test_project_id_reaches_model_inference_metadata(tmp_path: Path):
    class RecordingOrchestration(_Orchestration):
        def __init__(self):
            self.kwargs = None

        def run_task(self, **kwargs):
            self.kwargs = kwargs
            response = InferenceResponse(
                model="test/model",
                provider="test",
                content="project-scoped result",
                request_id="request-project",
                execution_id="execution-project",
                selected_model="test/model",
                provider_model="test/model",
                reported_model="test/model",
            )
            return TaskResult(task_id=kwargs["task_id"], role=kwargs["role"], response=response)

    orchestration = RecordingOrchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    result = engine.execute_one(
        DelegatedTask(
            "task-project",
            "researcher",
            "Inspect project A",
            project_id="project-a",
        )
    )
    assert result.status.value == "succeeded"
    assert orchestration.kwargs["project_id"] == "project-a"
    assert any(
        "project_id: project-a" in item["content"]
        for item in orchestration.kwargs["context"]
    )
