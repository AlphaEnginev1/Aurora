from __future__ import annotations

from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import json

import pytest

from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.agent_workbench import AgentWorkbenchToolLoop
from aurora.orchestration.director import DirectorPlan, DirectorPlanner
from aurora.orchestration.director_conversation import DirectorConversationService
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.director_runtime import DirectorRuntime, DirectorRuntimeError
from aurora.orchestration.mission_memory import MissionMemory
from aurora.orchestration.engine import OrchestrationEngine
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskEngineError, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.projects import ProjectRegistry, ProjectSpec, ProjectStatus
from aurora.workbench.models import WorkbenchRequest, WorkbenchResult
from aurora.workbench.service import UniversalWorkbench, WorkbenchError


def make_registry(tmp_path: Path) -> ProjectRegistry:
    alpha_workspace = tmp_path / "runtime" / "projects" / "alpha"
    beta_workspace = tmp_path / "runtime" / "projects" / "beta"
    return ProjectRegistry(
        {
            "project-alpha": ProjectSpec(
                project_id="project-alpha",
                name="Project Alpha",
                objective="Owns alpha files and tasks",
                status=ProjectStatus.ACTIVE,
                repository="AlphaEnginev1/alpha",
                workspace=str(alpha_workspace),
                current_state="Alpha workspace is registered",
                updated_at="2026-10-16T00:00:00Z",
            ),
            "project-beta": ProjectSpec(
                project_id="project-beta",
                name="Project Beta",
                objective="Owns beta files and tasks",
                status=ProjectStatus.ACTIVE,
                repository="AlphaEnginev1/beta",
                workspace=str(beta_workspace),
                current_state="Beta workspace is registered",
                updated_at="2026-10-16T00:00:00Z",
            ),
        }
    )


class CapturingOrchestration:
    def __init__(self):
        self.model_calls = []
        self.capability_calls = []

    def run_task(self, **kwargs):
        self.model_calls.append(kwargs)
        return SimpleNamespace(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=SimpleNamespace(content="project-scoped answer", model="fake/model"),
        )

    def run_capability_task(self, **kwargs):
        self.capability_calls.append(kwargs)
        return SimpleNamespace(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            execution=SimpleNamespace(status="succeeded", output={"ok": True}),
        )


class FakePlanner:
    def __init__(self, plan, project_registry):
        self.plan_value = plan
        self.project_registry = project_registry
        self.calls = []

    def plan(self, **kwargs):
        self.calls.append(kwargs)
        return self.plan_value


class CapturingDirectorProvider:
    provider_id = "test-provider"

    def __init__(self):
        self.calls = []

    def generate(self, *, messages, model, role, tools=(), metadata=None):
        self.calls.append(
            {"messages": messages, "model": model, "role": role, "tools": tools, "metadata": metadata}
        )
        return DirectorModelResponse(
            content='{"tasks":[{"task_id":"inspect","role":"operator","objective":"Inspect project"}]}',
            model="fake/model",
            provider="fake",
            request_id="request-1",
            execution_id="execution-1",
        )


def test_durable_mission_persists_and_inherits_canonical_project_scope(tmp_path):
    registry = make_registry(tmp_path)
    plan = DirectorPlan(
        objective="Inspect Project Alpha",
        tasks=(DelegatedTask("inspect", "operator", "Inspect registered files"),),
        model="fake/model",
    )
    planner = FakePlanner(plan, registry)
    orchestration = CapturingOrchestration()
    runtime_root = tmp_path / "runtime"
    task_engine = TaskEngine(
        orchestration,
        runtime_root,
        project_registry=registry,
    )
    store = DurableTaskStore(tmp_path / "tasks.db")
    service = DurableDirectorService(
        planner=planner,
        task_engine=task_engine,
        store=store,
    )

    submission = service.submit_mission(
        objective="Inspect Project Alpha",
        mission_id="mission-project-alpha",
        project_id="project-alpha",
        correlation_id="corr-project-alpha",
    )

    assert submission.project_id == "project-alpha"
    assert planner.calls[0]["project_id"] == "project-alpha"
    assert "project-alpha" in planner.calls[0]["context"][0]["content"]
    task = store.list_tasks("mission-project-alpha")[0].task
    assert task.project_id == "project-alpha"

    events = store.events("mission-project-alpha")
    submitted = next(event for event in events if event.event_type == "mission_submitted")
    enqueued = next(event for event in events if event.event_type == "enqueued")
    assert submitted.project_id == "project-alpha"
    assert submitted.payload["project_id"] == "project-alpha"
    assert submitted.correlation_id == "corr-project-alpha"
    assert enqueued.project_id == "project-alpha"
    assert enqueued.agent_role == "operator"

    # A newly planned phase with no explicit project field must inherit the
    # authoritative mission scope from its durable submission receipt.
    store.enqueue(
        "mission-project-alpha",
        (DelegatedTask("replanned", "operator", "Continue the project task"),),
        phase=1,
    )
    all_tasks = store.list_tasks("mission-project-alpha")
    assert {item.task.project_id for item in all_tasks} == {"project-alpha"}

    workspace = task_engine._workspace_for_task(
        task,
        "mission-project-alpha:inspect:attempt:1",
    )
    expected_root = Path(registry.resolve("project-alpha").workspace).resolve()
    assert workspace.is_relative_to(expected_root)
    assert workspace.parts[-2] == "tasks"

    task_engine.execute_one(
        task,
        execution_scope="mission-project-alpha:inspect:attempt:1",
    )
    assert orchestration.model_calls[0]["project_id"] == "project-alpha"

    capability_task = DelegatedTask(
        "list-project-files",
        "operator",
        "List registered project files",
        capability="workbench:file:list",
        args=(".",),
        project_id="project-alpha",
    )
    task_engine.execute_one(
        capability_task,
        execution_scope="mission-project-alpha:list-project-files:attempt:1",
    )
    request = orchestration.capability_calls[0]["request"]
    assert request.constraints["project_id"] == "project-alpha"
    assert request.constraints["project_repository"] == "AlphaEnginev1/alpha"


def test_project_scoped_task_fails_closed_without_registry_or_for_unknown_project(tmp_path):
    task = DelegatedTask(
        "inspect",
        "operator",
        "Inspect project files",
        project_id="missing-project",
    )
    with pytest.raises(TaskEngineError, match="canonical ProjectRegistry"):
        TaskEngine(CapturingOrchestration(), tmp_path / "runtime").validate((task,))

    registry = make_registry(tmp_path)
    with pytest.raises(TaskEngineError, match="invalid project scope"):
        TaskEngine(
            CapturingOrchestration(),
            tmp_path / "runtime",
            project_registry=registry,
        ).validate((task,))


def test_project_id_reaches_model_router_request_metadata():
    class CapturingRouter(ModelRouter):
        def __init__(self):
            super().__init__([ModelSpec("fake/model", "fake", "https://example.invalid")])
            self.requests = []

        def generate(self, request, capability=None, preferred_models=None):
            self.requests.append(request)
            return InferenceResponse(
                model="fake/model",
                provider="fake",
                content="completed",
                request_id=request.request_id,
                execution_id="execution-model-1",
            )

    router = CapturingRouter()
    orchestration = OrchestrationEngine(router)
    orchestration.run_task(
        task_id="task-alpha",
        role="engineer",
        objective="Inspect alpha",
        project_id="project-alpha",
        use_workbench=False,
    )
    assert router.requests[0].metadata["project_id"] == "project-alpha"


def test_director_planner_passes_project_scope_metadata_to_provider(tmp_path):
    registry = make_registry(tmp_path)
    provider = CapturingDirectorProvider()
    router = ModelRouter([ModelSpec("fake/model", "fake", "https://example.invalid")])
    planner = DirectorPlanner(
        router,
        project_registry=registry,
        provider=provider,
    )

    plan = planner.plan(
        objective="Inspect Project Alpha",
        project_id="project-alpha",
    )

    assert plan.tasks[0].task_id == "inspect"
    assert provider.calls[0]["metadata"] == {"project_id": "project-alpha"}


def test_workbench_project_scope_cannot_escape_registered_workspace(tmp_path):
    registry = make_registry(tmp_path)
    alpha_root = Path(registry.resolve("project-alpha").workspace)
    beta_root = Path(registry.resolve("project-beta").workspace)
    alpha_root.mkdir(parents=True)
    beta_root.mkdir(parents=True)
    (alpha_root / "alpha.txt").write_text("alpha", encoding="utf-8")
    (beta_root / "beta-secret.txt").write_text("beta", encoding="utf-8")

    workbench = UniversalWorkbench(
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        gateway=None,
        project_registry=registry,
    )
    request = WorkbenchRequest(
        capability="workbench:file:list",
        actor="operator",
        mission_id="mission-alpha",
        arguments=(".",),
        options={"project_id": "project-alpha"},
    )

    result = workbench.execute(request)
    assert result.status == "succeeded"
    names = {item["name"] for item in result.output}
    assert "alpha.txt" in names
    assert "beta-secret.txt" not in names

    with pytest.raises(WorkbenchError, match="outside the registered project workspace"):
        workbench._safe_path(str(beta_root / "beta-secret.txt"), request=request)


def test_conversation_scope_requires_one_uniquely_named_registered_project(tmp_path):
    service = object.__new__(DirectorConversationService)
    service.project_registry = make_registry(tmp_path)

    assert service._project_ids_in_prompt("Please inspect Project Alpha") == {"project-alpha"}
    assert service._project_id_from_prompt("Inspect Project Alpha") == "project-alpha"
    assert service._project_ids_in_prompt("Compare Project Alpha with Project Beta") == {
        "project-alpha",
        "project-beta",
    }
    assert service._project_id_from_prompt("Compare Project Alpha with Project Beta") is None


def test_specialist_workbench_tool_cannot_replace_runtime_project_scope():
    class FakeWorkbench:
        def __init__(self):
            self.calls = []
            self.session_store = object()

        def execute(self, request):
            self.calls.append(request)
            return WorkbenchResult(
                capability=request.capability,
                operation="read",
                status="succeeded",
                output="safe",
                provenance="fake-workbench",
            )

    class FakeRouter(ModelRouter):
        def __init__(self):
            super().__init__([ModelSpec("fake/model", "fake", "https://example.invalid")])
            self.responses = [
                '{"workbench_calls":[{"capability":"workbench:file:read","arguments":["README.md"],"options":{"project_id":"project-beta","work_id":"untrusted-work","idempotency_key":"untrusted-key"}}]}',
                "Finished with authoritative Workbench evidence.",
            ]

        def generate(self, request, capability=None, preferred_models=None):
            return InferenceResponse(
                model="fake/model",
                provider="fake",
                content=self.responses.pop(0),
                request_id=request.request_id,
                execution_id="agent-execution",
            )

    from aurora.inference.models import InferenceRequest

    workbench = FakeWorkbench()
    loop = AgentWorkbenchToolLoop(workbench)
    loop.run(
        request=InferenceRequest(
            messages=[{"role": "user", "content": "Read only the registered project files"}],
            metadata={
                "role": "engineer",
                "task_id": "task-alpha",
                "project_id": "project-alpha",
            },
        ),
        actor="engineer",
        mission_id="mission-alpha",
        task_id="task-alpha",
        reason="Inspect project files",
        router=FakeRouter(),
    )

    call = workbench.calls[0]
    assert call.options["project_id"] == "project-alpha"
    assert call.options["work_id"] == "mission-alpha"
    assert call.idempotency_key == "agent-task:mission-alpha:task-alpha:call:0"



def test_project_scoped_document_extract_cannot_read_another_project(tmp_path):
    registry = make_registry(tmp_path)
    alpha_root = Path(registry.resolve("project-alpha").workspace)
    beta_root = Path(registry.resolve("project-beta").workspace)
    alpha_root.mkdir(parents=True)
    beta_root.mkdir(parents=True)
    (beta_root / "private.txt").write_text("must not be read", encoding="utf-8")

    workbench = UniversalWorkbench(
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        gateway=None,
        project_registry=registry,
    )
    request = WorkbenchRequest(
        capability="workbench:document:extract",
        actor="operator",
        mission_id="mission-alpha",
        arguments=(str(beta_root / "private.txt"),),
        options={"project_id": "project-alpha"},
    )

    with pytest.raises(WorkbenchError, match="outside the registered project workspace"):
        workbench.execute(request)


def test_project_scoped_github_operation_cannot_target_another_repository(tmp_path):
    registry = make_registry(tmp_path)
    workbench = UniversalWorkbench(
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        gateway=None,
        project_registry=registry,
    )
    request = WorkbenchRequest(
        capability="workbench:github:file:write",
        actor="operator",
        mission_id="mission-alpha",
        arguments=(json.dumps({
            "owner": "AlphaEnginev1",
            "repo": "beta",
            "path": "README.md",
            "content": "not authorized",
            "message": "attempt cross-project write",
            "branch": "main",
        }),),
        options={"project_id": "project-alpha"},
    )

    with pytest.raises(WorkbenchError, match="outside the registered project repository"):
        workbench.execute(request)


def test_project_scoped_command_cannot_choose_another_workspace(tmp_path):
    registry = make_registry(tmp_path)
    alpha_root = Path(registry.resolve("project-alpha").workspace)
    beta_root = Path(registry.resolve("project-beta").workspace)

    class CapturingCapabilityRuntime:
        def __init__(self):
            self.calls = []

        def execute(self, request, *, workspace, args=()):
            self.calls.append((request, Path(workspace), tuple(args)))
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    runtime = CapturingCapabilityRuntime()
    workbench = UniversalWorkbench(
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        gateway=None,
        project_registry=registry,
        capability_runtime=runtime,
    )
    request = WorkbenchRequest(
        capability="workbench:command",
        actor="operator",
        mission_id="mission-alpha",
        arguments=(json.dumps({
            "capability": "python.package:example",
            "args": [],
            "workspace": str(beta_root),
        }),),
        options={"project_id": "project-alpha"},
    )

    with pytest.raises(WorkbenchError, match="outside the registered project workspace"):
        workbench.execute(request)
    assert runtime.calls == []



def test_project_scoped_command_passes_registered_identity_to_capability_runtime(tmp_path):
    registry = make_registry(tmp_path)
    alpha_root = Path(registry.resolve("project-alpha").workspace)
    alpha_root.mkdir(parents=True)

    class FakeBroker:
        def resolve(self, request):
            return object()

    class CapturingCapabilityRuntime:
        def __init__(self):
            self.broker = FakeBroker()
            self.calls = []

        def execute(self, request, *, workspace, args=(), environment=None):
            self.calls.append((request, Path(workspace), tuple(args), dict(environment or {})))
            return SimpleNamespace(
                returncode=0,
                timed_out=False,
                command=("fake-command",),
                stdout="ok",
                stderr="",
                capability=SimpleNamespace(artifact_id=None),
            )

    runtime = CapturingCapabilityRuntime()
    workbench = UniversalWorkbench(
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        gateway=None,
        project_registry=registry,
        capability_runtime=runtime,
    )
    request = WorkbenchRequest(
        capability="workbench:command",
        actor="operator",
        mission_id="mission-alpha",
        arguments=(json.dumps({
            "capability": "python.package:example",
            "args": [],
            "workspace": str(alpha_root),
        }),),
        options={"project_id": "project-alpha", "work_id": "mission-alpha"},
    )

    result = workbench.execute(request)

    assert result.status == "succeeded"
    executed_request, workspace, _, _ = runtime.calls[0]
    assert workspace == alpha_root.resolve()
    assert executed_request.constraints["project_id"] == "project-alpha"
    assert executed_request.constraints["project_repository"] == "AlphaEnginev1/alpha"



def test_project_scoped_artifact_intake_can_be_inspected_within_same_project(tmp_path):
    registry = make_registry(tmp_path)
    alpha_root = Path(registry.resolve("project-alpha").workspace)
    alpha_root.mkdir(parents=True)
    source = alpha_root / "source.txt"
    source.write_text("project-scoped artifact", encoding="utf-8")

    workbench = UniversalWorkbench(
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        gateway=None,
        project_registry=registry,
    )
    intake = workbench.execute(WorkbenchRequest(
        capability="workbench:artifact:intake",
        actor="operator",
        mission_id="mission-alpha",
        arguments=(str(source),),
        options={"project_id": "project-alpha"},
    ))
    artifact_path = Path(intake.output["path"])
    assert artifact_path.is_relative_to(alpha_root.resolve())

    inspected = workbench.execute(WorkbenchRequest(
        capability="workbench:artifact:inspect",
        actor="operator",
        mission_id="mission-alpha",
        arguments=(str(artifact_path),),
        options={"project_id": "project-alpha"},
    ))
    assert inspected.status == "succeeded"
    assert inspected.output["filename"].endswith(source.name)



def test_mission_evidence_returns_persisted_model_selection_and_execution_receipt():
    task = DelegatedTask(
        "inspect",
        "engineer",
        "Inspect the project",
        project_id="project-alpha",
    )
    durable_result = {
        "type": "model",
        "model": "nvidia/test-model",
        "provider": "nvidia_api",
        "request_id": "request-123",
        "execution_id": "execution-456",
        "requested_model": "nvidia/preferred-model",
        "selected_model": "nvidia/test-model",
        "reported_model": "nvidia/test-model",
        "content": "Inspection complete with verifiable evidence.",
        "truncated": False,
        "routing": {
            "selected_model": "nvidia/test-model",
            "score": 0.91,
            "factors": {"latency": 0.2, "capability": 0.9},
            "candidates": [{"model": "nvidia/test-model", "score": 0.91}],
        },
    }
    submission = SimpleNamespace(
        mission_id="mission-alpha",
        project_id="project-alpha",
        plan=SimpleNamespace(
            objective="Inspect the project",
            model="nvidia/planner-model",
            provider="nvidia_api",
            execution_id="planning-execution",
        ),
    )
    mission = SimpleNamespace(
        mission_id="mission-alpha",
        project_id="project-alpha",
        submission=submission,
        status=TaskStatus.SUCCEEDED,
        tasks=(SimpleNamespace(
            task=task,
            status=TaskStatus.SUCCEEDED,
            error=None,
            capability_result=None,
            model_result=None,
            durable_result=durable_result,
        ),),
        wait_timed_out=False,
        events=(),
    )

    evidence = DirectorConversationService._mission_evidence(mission)

    assert evidence["project_id"] == "project-alpha"
    assert evidence["provider"] == "nvidia_api"
    assert evidence["planning_execution_id"] == "planning-execution"
    assert evidence["tasks"][0]["project_id"] == "project-alpha"
    assert evidence["tasks"][0]["model"] == "nvidia/test-model"
    assert evidence["tasks"][0]["provider"] == "nvidia_api"
    assert evidence["tasks"][0]["request_id"] == "request-123"
    assert evidence["tasks"][0]["execution_id"] == "execution-456"
    assert evidence["tasks"][0]["selected_model"] == "nvidia/test-model"
    assert evidence["tasks"][0]["routing"]["score"] == 0.91
    assert evidence["tasks"][0]["result"] == "Inspection complete with verifiable evidence."



def test_project_scoped_github_read_accepts_only_registered_repo_api_paths(tmp_path):
    registry = make_registry(tmp_path)
    workbench = UniversalWorkbench(
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        gateway=None,
        project_registry=registry,
    )
    valid = WorkbenchRequest(
        capability="workbench:github:get",
        actor="operator",
        mission_id="mission-alpha",
        arguments=("/repos/AlphaEnginev1/alpha/issues",),
        options={"project_id": "project-alpha"},
    )
    workbench._enforce_project_repository_scope(valid)

    invalid = WorkbenchRequest(
        capability="workbench:github:get",
        actor="operator",
        mission_id="mission-alpha",
        arguments=("/repos/AlphaEnginev1/beta/issues",),
        options={"project_id": "project-alpha"},
    )
    with pytest.raises(WorkbenchError, match="outside the registered project repository"):
        workbench._enforce_project_repository_scope(invalid)



def test_mission_evidence_trusts_terminal_director_success_after_replanning():
    old_task = DelegatedTask("first-pass", "engineer", "First pass")
    replanned_task = DelegatedTask("r1_recovery", "engineer", "Recover failed first pass")
    submission = SimpleNamespace(
        mission_id="mission-replanned",
        project_id=None,
        plan=SimpleNamespace(
            objective="Complete the order",
            model="nvidia/planner",
            provider="nvidia_api",
            execution_id="planning-1",
        ),
    )
    mission = SimpleNamespace(
        mission_id="mission-replanned",
        submission=submission,
        status=TaskStatus.SUCCEEDED,
        tasks=(
            SimpleNamespace(
                task=old_task,
                status=TaskStatus.FAILED,
                error="transient failure in the original phase",
                durable_result={"type": "model", "content": "failed attempt"},
            ),
            SimpleNamespace(
                task=replanned_task,
                status=TaskStatus.SUCCEEDED,
                error=None,
                durable_result={"type": "model", "model": "nvidia/reserve", "content": "replanned result"},
            ),
        ),
        wait_timed_out=False,
        events=(),
    )

    evidence = DirectorConversationService._mission_evidence(mission)

    assert [item["status"] for item in evidence["tasks"]] == ["failed", "succeeded"]
    assert evidence["status"] == "succeeded"



def test_project_scoped_research_task_carries_registered_context_and_constraints(tmp_path):
    registry = make_registry(tmp_path)

    class CapturingResearchService:
        def __init__(self):
            self.requests = []

        def discover(self, request):
            self.requests.append(("discover", request))
            return ("https://example.test/alpha",)

        def retrieve_and_extract(self, request):
            self.requests.append(("retrieve", request))
            return (), ()

    research = CapturingResearchService()
    engine = TaskEngine(
        CapturingOrchestration(),
        tmp_path / "runtime",
        project_registry=registry,
        research_service=research,
    )
    task = DelegatedTask(
        "research-alpha",
        "researcher",
        "Research the registered project's integration constraints",
        capability="research",
        project_id="project-alpha",
    )
    engine.execute_one(task, execution_scope="mission-alpha:research-alpha:attempt:1")

    discovery_request = next(request for kind, request in research.requests if kind == "discover")
    retrieval_request = next(request for kind, request in research.requests if kind == "retrieve")
    for request in (discovery_request, retrieval_request):
        assert "project-alpha" in request.objective
        assert "AlphaEnginev1/alpha" in request.objective
        assert "project_id=project-alpha" in request.constraints
        assert "project_repository=AlphaEnginev1/alpha" in request.constraints


def test_project_scoped_workbench_research_overrides_model_project_constraints(tmp_path):
    registry = make_registry(tmp_path)
    workbench = UniversalWorkbench(
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        gateway=None,
        project_registry=registry,
    )
    request = WorkbenchRequest(
        capability="workbench:research:discover",
        actor="researcher",
        mission_id="mission-alpha",
        arguments=(json.dumps({
            "objective": "Find integration guidance",
            "constraints": ["project_id=project-beta", "project_repository=AlphaEnginev1/beta", "fresh_only=true"],
            "max_sources": 5,
        }),),
        options={"project_id": "project-alpha"},
    )

    normalized = workbench._research_request(request)

    assert "project-alpha" in normalized.objective
    assert "AlphaEnginev1/alpha" in normalized.objective
    assert "project_id=project-alpha" in normalized.constraints
    assert "project_repository=AlphaEnginev1/alpha" in normalized.constraints
    assert "project_id=project-beta" not in normalized.constraints
    assert "project_repository=AlphaEnginev1/beta" not in normalized.constraints
    assert "fresh_only=true" in normalized.constraints



def test_runtime_recovery_reprojects_legacy_mailbox_rows_with_canonical_project_scope(tmp_path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    memory = MissionMemory(store)
    task = DelegatedTask(
        "inspect",
        "operator",
        "Inspect Project Alpha",
        project_id="project-alpha",
    )
    store.enqueue(
        "mission-project-alpha",
        (task,),
        correlation_id="corr-project-alpha",
        project_id="project-alpha",
        initial_events=(
            {
                "event_type": "mission_submitted",
                "project_id": "project-alpha",
                "correlation_id": "corr-project-alpha",
                "payload": {
                    "objective": "Inspect Project Alpha",
                    "project_id": "project-alpha",
                    "correlation_id": "corr-project-alpha",
                    "model": "nvidia/planner",
                    "provider": "nvidia_api",
                },
            },
        ),
    )
    claimed = store.claim_next("mission-project-alpha", "worker-test")
    assert claimed is not None
    store.complete(
        "mission-project-alpha",
        "inspect",
        status="succeeded",
        result={
            "type": "model",
            "model": "nvidia/reserve",
            "provider": "nvidia_api",
            "request_id": "request-task",
            "execution_id": "execution-task",
            "content": "The project inspection is complete.",
        },
        worker_id="worker-test",
    )

    # Model a legacy or interrupted projection that wrote to mission memory
    # before project_id was carried into the mailbox.
    memory.append(
        mission_id="mission-project-alpha",
        source="director",
        kind="mission_objective",
        content="Inspect Project Alpha",
        metadata={"legacy": True},
    )
    memory.append(
        mission_id="mission-project-alpha",
        source="director",
        kind="mission_plan",
        content="Inspect files",
        metadata={"legacy": True},
    )
    memory.append(
        mission_id="mission-project-alpha",
        source="worker:legacy",
        kind="task_result",
        content="legacy completion",
        metadata={"task_id": "inspect", "status": "succeeded"},
        task_id="inspect",
    )

    runtime = object.__new__(DirectorRuntime)
    runtime._handoff_lock = RLock()
    runtime.store = store
    runtime.memory = memory
    runtime.worker_id = "worker-recovered"

    assert runtime._ensure_mission_memory("mission-project-alpha") is True

    objective = memory.first_of_kind(
        "mission-project-alpha", "mission_objective", project_id="project-alpha"
    )
    plan = memory.first_of_kind(
        "mission-project-alpha", "mission_plan", project_id="project-alpha"
    )
    task_result = memory.first_of_kind(
        "mission-project-alpha", "task_result", project_id="project-alpha"
    )
    assert objective is not None and objective.project_id == "project-alpha"
    assert plan is not None and plan.project_id == "project-alpha"
    assert task_result is not None and task_result.project_id == "project-alpha"
    assert "nvidia/reserve" in task_result.content
    assert memory.task_result_task_ids(
        "mission-project-alpha", project_id="project-alpha"
    ) == frozenset({"inspect"})
    assert memory.first_of_kind("mission-project-alpha", "mission_objective").metadata["legacy"] is True

    events = store.events("mission-project-alpha")
    submitted = next(event for event in events if event.event_type == "mission_submitted")
    completed = next(event for event in events if event.event_type == "succeeded")
    assert submitted.project_id == "project-alpha"
    assert completed.project_id == "project-alpha"
    assert completed.correlation_id == "corr-project-alpha"



def test_director_replanning_preserves_registered_project_and_rejects_scope_change(tmp_path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue(
        "mission-project-alpha",
        (DelegatedTask("inspect", "operator", "Inspect Alpha", project_id="project-alpha"),),
        project_id="project-alpha",
        initial_events=(
            {
                "event_type": "mission_submitted",
                "project_id": "project-alpha",
                "payload": {"objective": "Inspect Project Alpha", "project_id": "project-alpha"},
            },
        ),
    )
    runtime = object.__new__(DirectorRuntime)
    runtime.store = store

    planned = runtime._phase_tasks(
        "mission-project-alpha",
        1,
        (
            DelegatedTask("verify", "verifier", "Verify the project result"),
        ),
    )
    assert planned[0].task_id == "r1_verify"
    assert planned[0].project_id == "project-alpha"

    with pytest.raises(DirectorRuntimeError, match="change project scope"):
        runtime._phase_tasks(
            "mission-project-alpha",
            2,
            (
                DelegatedTask(
                    "cross-project",
                    "engineer",
                    "Change another project's file",
                    project_id="project-beta",
                ),
            ),
        )
