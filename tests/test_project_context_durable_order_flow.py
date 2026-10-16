from pathlib import Path

import pytest

from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.durable_director import DurableDirectorError, DurableDirectorService
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.event_bus import DurableEventBus
from aurora.inference.models import InferenceResponse
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_worker import WorkerPolicy
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


class _ReplanningPlanner(_Planner):
    def __init__(self):
        super().__init__()
        self.review_calls = 0
        self.review_contexts = []

    def review(self, *, objective, context):
        self.review_calls += 1
        self.review_contexts.append(context)
        if self.review_calls == 1:
            return DirectorReview(
                decision="continue",
                rationale="Validate the project-specific result before completion.",
                tasks=(
                    DelegatedTask(
                        "validate",
                        "researcher",
                        "Validate the durable result for Project A",
                    ),
                ),
                model="test/model",
            )
        return DirectorReview(
            decision="complete",
            rationale="Project-specific evidence is sufficient.",
            tasks=(),
            model="test/model",
        )


class _RecordingOrchestration(_Orchestration):
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
        task_id = kwargs["task_id"]
        response = InferenceResponse(
            model="test/model",
            provider="test",
            content=f"verified result for {task_id}",
            request_id=f"request-{task_id}",
            execution_id=f"execution-{task_id}",
            selected_model="test/model",
            provider_model="test/model",
            reported_model="test/model",
        )
        return TaskResult(task_id=task_id, role=kwargs["role"], response=response)


def _make_project_runtime(
    tmp_path: Path,
    store: DurableTaskStore,
    planner: _ReplanningPlanner,
    orchestration: _RecordingOrchestration,
) -> DirectorRuntime:
    bus = DurableEventBus(store)
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    service = DurableDirectorService(
        planner=planner,
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    return DirectorRuntime(
        director=service,
        store=store,
        event_bus=bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=2,
            idle_sleep_seconds=0,
        ),
        worker_id="director-project-flow",
        event_consumer_id="director-project-flow-events",
        idle_sleep_seconds=0,
        adversarial_review_enabled=False,
    )


def test_project_replanning_and_restart_replay_preserve_one_mission_and_context(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "mission-replan.db")
    planner_before_restart = _ReplanningPlanner()
    orchestration_before_restart = _RecordingOrchestration()
    runtime = _make_project_runtime(
        tmp_path,
        store,
        planner_before_restart,
        orchestration_before_restart,
    )
    submission = runtime.submit_mission(
        objective="Audit Project A",
        mission_id="mission-project-replan",
        correlation_id="corr-project-replan",
        project_id="project-a",
    )

    real_enqueue = store.enqueue
    interruption = {"raised": False}

    def interrupt_before_follow_up_commit(mission_id, tasks, **kwargs):
        if kwargs.get("phase", 0) == 1 and not interruption["raised"]:
            interruption["raised"] = True
            raise RuntimeError("simulated interruption before follow-up transaction")
        return real_enqueue(mission_id, tasks, **kwargs)

    store.enqueue = interrupt_before_follow_up_commit
    with pytest.raises(RuntimeError, match="simulated interruption"):
        runtime.run_until_terminal(submission, max_cycles=20)
    store.enqueue = real_enqueue

    events_after_interruption = store.events(submission.mission_id)
    first_review = next(
        event for event in events_after_interruption
        if event.event_type == "director_review"
    )
    assert first_review.payload["decision"] == "continue"
    assert first_review.payload["project_id"] == "project-a"
    assert first_review.project_id == "project-a"
    assert len(first_review.payload["follow_up_tasks"]) == 1
    assert first_review.payload["follow_up_tasks"][0]["task_id"] == "r1_validate"
    assert first_review.payload["follow_up_tasks"][0]["project_id"] == "project-a"
    assert not any(event.event_type == "mission_reopened" for event in events_after_interruption)
    assert [item.task.task_id for item in store.list_tasks(submission.mission_id)] == ["task-a"]

    # A fresh Director instance sees the committed review decision and restores
    # that exact approved graph instead of asking for a second round-1 plan.
    planner_after_restart = _ReplanningPlanner()
    orchestration_after_restart = _RecordingOrchestration()
    restarted = _make_project_runtime(
        tmp_path,
        store,
        planner_after_restart,
        orchestration_after_restart,
    )
    result = restarted.run_until_terminal(submission, max_cycles=50)

    assert result.status is TaskStatus.SUCCEEDED
    stored = store.list_tasks(submission.mission_id)
    assert [item.task.task_id for item in stored] == ["task-a", "r1_validate"]
    assert all(item.task.project_id == "project-a" for item in stored)
    assert [call["task_id"] for call in orchestration_before_restart.calls] == ["task-a"]
    assert [call["task_id"] for call in orchestration_after_restart.calls] == ["r1_validate"]
    assert all(call["project_id"] == "project-a" for call in (
        orchestration_before_restart.calls + orchestration_after_restart.calls
    ))
    assert all(
        any(
            "project_id: project-a" in item["content"]
            for item in call["context"]
        )
        for call in (orchestration_before_restart.calls + orchestration_after_restart.calls)
    )

    # Both Director reviews, including the post-restart review, receive the
    # canonical project record rather than only old mailbox text.
    review_contexts = (
        planner_before_restart.review_contexts + planner_after_restart.review_contexts
    )
    assert len(review_contexts) == 2
    for context in review_contexts:
        project_context = next(
            item["content"] for item in context
            if item["content"].startswith("Authoritative Aurora project context")
        )
        assert '"project_id": "project-a"' in project_context
        assert "canonical state A" in project_context

    final_events = store.events(submission.mission_id)
    reviews = [event for event in final_events if event.event_type == "director_review"]
    assert [event.payload["round"] for event in reviews] == [1, 2]
    assert len([
        event for event in final_events
        if event.event_type == "mission_submitted"
    ]) == 1
    assert len([
        event for event in final_events
        if event.event_type == "mission_reopened" and event.payload.get("round") == 1
    ]) == 1
    assert len([
        event for event in final_events
        if event.event_type == "director_decision_executed"
        and event.payload.get("round") == 1
        and event.payload.get("execution") == "follow_up_tasks_enqueued"
    ]) == 1
    assert len([
        event for event in final_events
        if event.event_type == "mission_succeeded"
    ]) == 1
    assert all(
        event.project_id == "project-a"
        for event in final_events
        if event.event_type in {
            "mission_submitted",
            "director_review",
            "director_review_started",
            "director_decision_executed",
            "mission_reopened",
            "enqueued",
        }
    )
    assert all(
        event.correlation_id == "corr-project-replan"
        for event in final_events
        if event.event_type in {
            "mission_submitted",
            "director_review",
            "director_review_started",
            "director_decision_executed",
            "mission_reopened",
            "enqueued",
        }
    )
