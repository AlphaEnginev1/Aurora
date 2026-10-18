from pathlib import Path

import pytest

from aurora.orchestration.director import DirectorPlan
from aurora.orchestration.durable_director import DurableDirectorError, DurableDirectorService
from aurora.inference.models import InferenceResponse
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskEngineError
from aurora.orchestration.team_runtime import TeamCoordinator
from aurora.organization.agent_team import AgentTeam, TeamMember
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
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace", project_registry=planner.project_registry),
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


def test_legacy_event_mailbox_projection_is_enriched_with_canonical_context(
    tmp_path: Path,
):
    import json
    from aurora.orchestration.mission_memory import MissionMemory

    store = DurableTaskStore(tmp_path / "legacy-mailbox.db")
    mission_id = "mission-legacy-mailbox"
    project_id = "project-a"
    correlation_id = "corr-legacy-mailbox"
    store.enqueue(
        mission_id,
        [
            DelegatedTask(
                "task-a",
                "researcher",
                "inspect project A",
                project_id=project_id,
            )
        ],
        correlation_id=correlation_id,
        initial_events=(
            {
                "event_type": "mission_submitted",
                "payload": {
                    "objective": "inspect project A",
                    "project_id": project_id,
                    "correlation_id": correlation_id,
                },
                "project_id": project_id,
                "correlation_id": correlation_id,
            },
        ),
    )
    event = store.append_event(
        mission_id=mission_id,
        task_id="task-a",
        event_type="skill.evaluated",
        payload={"score": 1},
    )

    # Simulate an event/mailbox row written by a pre-context-propagation runtime.
    with store._connect() as db:
        db.execute(
            """
            UPDATE task_events
            SET project_id = NULL, agent_role = NULL, correlation_id = NULL
            WHERE event_id = ?
            """,
            (event.event_id,),
        )

    memory = MissionMemory(store)
    metadata = {"event_id": event.event_id, "task_id": "task-a"}
    content = json.dumps({"score": 1}, sort_keys=True)
    with store._connect() as db:
        db.execute(
            """
            INSERT INTO mission_messages (
                mission_id, source, kind, content, metadata_json, created_at,
                scope, project_id, agent_role, task_id, event_id, correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL)
            """,
            (
                mission_id,
                "skill-learning",
                "skill.evaluated",
                content,
                json.dumps(metadata, sort_keys=True),
                "2026-10-01T00:00:00+00:00",
                "mission",
                event.event_id,
            ),
        )

    repaired = memory.append(
        mission_id=mission_id,
        source="skill-learning",
        kind="skill.evaluated",
        content=content,
        metadata=metadata,
        event_id=event.event_id,
    )
    assert repaired.project_id == project_id
    assert repaired.agent_role == "researcher"
    assert repaired.task_id == "task-a"
    assert repaired.correlation_id == correlation_id
    projected = memory.list_project(project_id, limit=100)
    assert any(item.event_id == event.event_id for item in projected)


def test_project_scoped_mission_rejects_unknown_project(tmp_path: Path):
    planner = _Planner()
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace", project_registry=planner.project_registry),
        store=DurableTaskStore(tmp_path / "missions.db"),
    )
    with pytest.raises(DurableDirectorError, match="unknown project_id"):
        service.submit_mission(objective="Bad project", project_id="not-registered")


def test_initial_task_graph_rejects_conflicting_project_id_before_persisting(tmp_path: Path):
    planner = _Planner()
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace", project_registry=planner.project_registry),
        store=store,
    )

    with pytest.raises(
        DurableDirectorError,
        match="conflicts with authoritative mission project",
    ):
        service.submit_mission(
            objective="Audit Project A",
            mission_id="mission-project-conflict",
            correlation_id="corr-project-conflict",
            project_id="project-a",
            tasks=(
                DelegatedTask(
                    "task-b",
                    "researcher",
                    "Inspect Project B",
                    project_id="project-b",
                ),
            ),
        )

    assert planner.calls == []
    assert store.list_tasks("mission-project-conflict") == ()
    assert store.events("mission-project-conflict") == ()


def test_mission_idempotency_cannot_switch_project(tmp_path: Path):
    planner = _Planner()
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace", project_registry=planner.project_registry),
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

def test_task_engine_auto_role_resolution_preserves_project_id(
    tmp_path: Path, monkeypatch
):
    from aurora.organization import agent_selection

    class FixedSelector:
        def __init__(self, *_args):
            pass

        def select(self, **_kwargs):
            return "researcher"

    monkeypatch.setattr(agent_selection, "AgentSelector", FixedSelector)
    engine = TaskEngine(_Orchestration(), tmp_path / "workspace", organization=object())
    resolved = engine._resolve_agent_task(
        DelegatedTask(
            "auto-task",
            "auto",
            "Inspect project A",
            project_id="project-a",
        )
    )
    assert resolved.role == "researcher"
    assert resolved.project_id == "project-a"


def test_team_coordinator_auto_role_resolution_preserves_project_id(
    monkeypatch,
):
    import aurora.orchestration.team_runtime as team_runtime

    class FakeOrganization:
        @staticmethod
        def resolve_agent(role):
            return type("Agent", (), {"department": "engineering"})()

    class FakeAgentSelector:
        organization = FakeOrganization()

        @staticmethod
        def route(**_kwargs):
            return type("Route", (), {"selected_role": "researcher"})()

    class FakeTaskEngine:
        @staticmethod
        def validate(tasks):
            return tuple(tasks)

    class FakeTeamSelector:
        def __init__(self, *_args):
            pass

        @staticmethod
        def form(**kwargs):
            return AgentTeam(
                objective=kwargs["objective"],
                members=(
                    TeamMember(
                        role="researcher",
                        score=1.0,
                        covered_skills=(),
                        department="engineering",
                    ),
                ),
                required_skills=tuple(kwargs.get("required_skill_ids", ())),
                covered_skills=(),
                missing_skills=(),
                max_members=4,
            )

    monkeypatch.setattr(
        team_runtime, "DynamicAgentTeamSelector", FakeTeamSelector
    )
    coordinator = TeamCoordinator(
        task_engine=FakeTaskEngine(),
        agent_selector=FakeAgentSelector(),
    )
    state = coordinator.prepare(
        mission_id="mission-auto-project",
        objective="Inspect project A",
        tasks=(
            DelegatedTask(
                "auto-task",
                "auto",
                "Inspect project A",
                project_id="project-a",
            ),
        ),
    )
    assert state.tasks[0].role == "researcher"
    assert state.tasks[0].project_id == "project-a"


def test_task_engine_requires_registered_active_project_scope(tmp_path: Path):
    projects = {
        "project-a": ProjectSpec(
            project_id="project-a",
            name="Project A",
            objective="Active project",
            status=ProjectStatus.ACTIVE,
            repository="org/project-a",
            workspace=str(tmp_path / "projects" / "a"),
        ),
    }
    inactive_status = next(
        status for status in ProjectStatus if status is not ProjectStatus.ACTIVE
    )
    projects["project-paused"] = ProjectSpec(
        project_id="project-paused",
        name="Paused project",
        objective="Must not execute",
        status=inactive_status,
        repository="org/project-paused",
        workspace=str(tmp_path / "projects" / "paused"),
    )
    registry = ProjectRegistry(projects)

    task = DelegatedTask(
        "inspect",
        "researcher",
        "Inspect registered project",
        project_id="project-a",
    )
    with pytest.raises(TaskEngineError, match="canonical ProjectRegistry"):
        TaskEngine(_Orchestration(), tmp_path / "workspace").validate((task,))

    engine = TaskEngine(
        _Orchestration(),
        tmp_path / "workspace",
        project_registry=registry,
    )
    with pytest.raises(TaskEngineError, match="invalid project scope"):
        engine.validate((
            DelegatedTask(
                "unknown",
                "researcher",
                "Inspect unknown project",
                project_id="not-registered",
            ),
        ))
    with pytest.raises(TaskEngineError, match="requires an active project"):
        engine.validate((
            DelegatedTask(
                "paused",
                "researcher",
                "Inspect paused project",
                project_id="project-paused",
            ),
        ))

    assert engine.validate((task,))[0].project_id == "project-a"


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
    engine = TaskEngine(orchestration, tmp_path / "workspace", project_registry=_Planner().project_registry)
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

def test_initial_task_graph_rejects_unknown_task_project_id_without_mission_scope(
    tmp_path: Path,
):
    planner = _Planner()
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace"),
        store=store,
    )

    with pytest.raises(DurableDirectorError, match="references unknown project_id"):
        service.submit_mission(
            objective="Inspect an unregistered project",
            mission_id="mission-unknown-task-project",
            correlation_id="corr-unknown-task-project",
            tasks=(
                DelegatedTask(
                    "task-unknown",
                    "researcher",
                    "Inspect unknown project",
                    project_id="missing-project",
                ),
            ),
        )

    assert planner.calls == []
    assert store.list_tasks("mission-unknown-task-project") == ()
    assert store.events("mission-unknown-task-project") == ()


def test_registered_task_project_id_is_valid_without_mission_project_scope(tmp_path: Path):
    planner = _Planner()
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(
            _Orchestration(),
            tmp_path / "workspace",
            project_registry=planner.project_registry,
        ),
        store=store,
    )

    submission = service.submit_mission(
        objective="Inspect registered Project A",
        mission_id="mission-task-project-a",
        correlation_id="corr-task-project-a",
        tasks=(
            DelegatedTask(
                "task-project-a",
                "researcher",
                "Inspect Project A",
                project_id="project-a",
            ),
        ),
    )

    stored = store.list_tasks(submission.mission_id)
    assert len(stored) == 1
    assert stored[0].task.project_id == "project-a"
    event = next(
        item for item in store.events(submission.mission_id)
        if item.event_type == "mission_submitted"
    )
    assert event.payload["task_graph"][0]["project_id"] == "project-a"


def test_planner_generated_unknown_task_project_id_is_rejected_before_persistence(
    tmp_path: Path,
):
    planner = _Planner()
    planner.plan = lambda *, objective, context=None, model=None: DirectorPlan(
        objective=objective,
        tasks=(
            DelegatedTask(
                "task-unknown",
                "researcher",
                "Inspect unregistered project",
                project_id="missing-project",
            ),
        ),
        model="test/model",
    )
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(_Orchestration(), tmp_path / "workspace"),
        store=store,
    )

    with pytest.raises(DurableDirectorError, match="references unknown project_id"):
        service.submit_mission(
            objective="Inspect unregistered project",
            mission_id="mission-planner-unknown-task-project",
            correlation_id="corr-planner-unknown-task-project",
        )

    assert store.list_tasks("mission-planner-unknown-task-project") == ()
    assert store.events("mission-planner-unknown-task-project") == ()


def test_idempotent_replay_revalidates_task_project_against_canonical_registry(
    tmp_path: Path,
):
    planner = _Planner()
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(
            _Orchestration(),
            tmp_path / "workspace",
            project_registry=planner.project_registry,
        ),
        store=store,
    )
    tasks = (
        DelegatedTask(
            "task-project-a",
            "researcher",
            "Inspect Project A",
            project_id="project-a",
        ),
    )

    first = service.submit_mission(
        objective="Inspect registered Project A",
        mission_id="mission-project-replay-validation",
        correlation_id="corr-project-replay-validation",
        tasks=tasks,
    )
    assert first.mission_id == "mission-project-replay-validation"
    assert len(store.list_tasks(first.mission_id)) == 1

    # Simulate the canonical project configuration changing between the
    # original accepted order and a browser/request replay.
    planner.project_registry.projects.pop("project-a")

    with pytest.raises(DurableDirectorError, match="references unknown project_id"):
        service.submit_mission(
            objective="Inspect registered Project A",
            mission_id="mission-project-replay-validation",
            correlation_id="corr-project-replay-validation",
            tasks=tasks,
        )

    assert len(store.list_tasks("mission-project-replay-validation")) == 1
    assert sum(
        event.event_type == "mission_submitted"
        for event in store.events("mission-project-replay-validation")
    ) == 1

def test_task_engine_rejects_project_workspace_outside_authorized_roots(
    tmp_path: Path,
):
    allowed_root = tmp_path / "runtime"
    external_workspace = tmp_path / "external-project"
    allowed_root.mkdir()
    registry = ProjectRegistry({
        "project-external": ProjectSpec(
            project_id="project-external",
            name="External Project",
            objective="Must stay within authorized workspace roots",
            status=ProjectStatus.ACTIVE,
            workspace=str(external_workspace),
        ),
    })
    engine = TaskEngine(
        _Orchestration(),
        allowed_root,
        project_registry=registry,
        allowed_workspace_roots=(allowed_root,),
    )

    with pytest.raises(
        TaskEngineError,
        match="outside allowed TaskEngine roots",
    ):
        engine._project_workspace_root("project-external")

    # Resolution/validation must fail before the external path is created.
    assert not external_workspace.exists()

def _add_paused_project(planner: _Planner) -> None:
    planner.project_registry.projects["project-paused"] = ProjectSpec(
        project_id="project-paused",
        name="Paused Project",
        objective="Must not accept new scoped work",
        status=ProjectStatus.PAUSED,
        repository="org/project-paused",
        current_state="paused for safety",
    )


def test_durable_submission_rejects_inactive_mission_project_before_planning(
    tmp_path: Path,
):
    planner = _Planner()
    _add_paused_project(planner)
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(
            _Orchestration(),
            tmp_path / "workspace",
            project_registry=planner.project_registry,
        ),
        store=store,
    )

    with pytest.raises(DurableDirectorError, match="must be active before mission submission"):
        service.submit_mission(
            objective="Inspect paused project",
            mission_id="mission-paused-project",
            correlation_id="corr-paused-project",
            project_id="project-paused",
        )

    assert planner.calls == []
    assert store.list_tasks("mission-paused-project") == ()
    assert store.events("mission-paused-project") == ()


def test_durable_submission_rejects_inactive_task_project_before_persistence(
    tmp_path: Path,
):
    planner = _Planner()
    _add_paused_project(planner)
    store = DurableTaskStore(tmp_path / "missions.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=TaskEngine(
            _Orchestration(),
            tmp_path / "workspace",
            project_registry=planner.project_registry,
        ),
        store=store,
    )

    with pytest.raises(DurableDirectorError, match="requires an active project_id"):
        service.submit_mission(
            objective="Inspect paused task project",
            mission_id="mission-paused-task-project",
            correlation_id="corr-paused-task-project",
            tasks=(
                DelegatedTask(
                    "task-paused",
                    "researcher",
                    "Inspect paused project",
                    project_id="project-paused",
                ),
            ),
        )

    assert planner.calls == []
    assert store.list_tasks("mission-paused-task-project") == ()
    assert store.events("mission-paused-task-project") == ()

