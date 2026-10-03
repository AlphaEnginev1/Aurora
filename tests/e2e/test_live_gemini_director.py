import os

import pytest

from aurora.autonomy.acquisition import AcquisitionResult
from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.capability_runtime import CapabilityRuntime
from aurora.autonomy.models import CapabilityRequest
from aurora.autonomy.policy import Policy
from aurora.autonomy.registry import ToolSpec
from aurora.autonomy.sandbox import SandboxResult
from aurora.inference.models import InferenceRequest, InferenceResponse
from aurora.orchestration.director import DirectorPlanner, DirectorService
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy
from aurora.orchestration.director_provider import DirectorModelResponse, DirectorToolSpec
from aurora.orchestration.engine import OrchestrationEngine
from aurora.orchestration.gemini_provider import GeminiDirectorProvider
from aurora.orchestration.task_engine import TaskEngine, TaskStatus
from aurora.organization.registry import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.skills.models import SkillSpec
from aurora.skills.registry import SkillRegistry
from aurora.skills.learning import SkillLearningStore
from aurora.inference.model_performance import ModelPerformanceStore


@pytest.fixture(scope="module")
def live_gemini_response() -> DirectorModelResponse:
    if os.getenv("NVIDIA_GEMINI_LIVE") != "1":
        pytest.skip("live Gemini gate is opt-in outside the dedicated workflow")
    if not os.getenv("GEMINI_API_KEY"):
        raise AssertionError(
            "LIVE-GEMINI gate requires GEMINI_API_KEY; missing live credential must fail the gate"
        )
    if not os.getenv("GEMINI_MODEL_ID"):
        raise AssertionError(
            "LIVE-GEMINI gate requires GEMINI_MODEL_ID; missing served model must fail the gate"
        )

    provider = GeminiDirectorProvider()
    return provider.generate(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Aurora's Director. You may only propose work through the "
                    "delegate_task tool. Do not answer with prose."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Create exactly one research task to investigate Aurora architecture. "
                    "Set capability exactly to tool:git. The task must use the registered "
                    "git capability; do not omit capability."
                ),
            },
        ],
        model=None,
        role="director_planner",
        tools=(
            DirectorToolSpec(
                name="delegate_task",
                description=(
                    "Request Aurora to validate and enqueue one delegated task using a "
                    "registered capability."
                ),
                parameters={
                    "type": "object",
                    "required": ["task_id", "role", "objective", "capability"],
                    "properties": {
                        "task_id": {"type": "string"},
                        "role": {"type": "string"},
                        "objective": {"type": "string"},
                        "capability": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
        ),
    )


@pytest.mark.live
def test_live_gemini_director_tool_call(live_gemini_response):
    response = live_gemini_response

    assert response.provider == "gemini"
    assert response.tool_calls
    assert response.tool_calls[0].name == "delegate_task"
    assert response.tool_calls[0].arguments["task_id"]
    assert response.tool_calls[0].arguments["role"] == "engineer"
    assert response.tool_calls[0].arguments["capability"] == "tool:git"


class ReplayDirectorProvider:
    """Replay a real Gemini response without issuing a second external API call."""

    provider_id = "gemini"

    def __init__(self, response: DirectorModelResponse):
        self.response = response

    def generate(self, messages, model, role, tools=()):
        return self.response


class ControlledRouter:
    """Deterministic model boundary retained for non-capability execution checks."""

    def __init__(self):
        self.requests: list[InferenceRequest] = []

    def generate(self, request, *, capability="reasoning", preferred_models=None):
        self.requests.append(request)
        return InferenceResponse(
            model="controlled-task-model",
            provider="controlled",
            content=(
                "controlled execution result\n"
                "VERIFY:result_present=pass\n"
                "QUALITY:quality_ok=pass"
            ),
            request_id=request.request_id,
            execution_id="controlled-execution",
        )


class ControlledCapabilityAcquisition:
    """Return a verified registered artifact without downloading anything."""

    def __init__(self, executable):
        self.executable = executable
        self.calls: list[str] = []

    def acquire(self, spec):
        self.calls.append(spec.name)
        return AcquisitionResult(
            spec.name,
            "installed",
            spec.source,
            spec.executable,
            str(self.executable),
            version=spec.version,
            artifact_sha256="a" * 64,
            verification="verified",
        )


class ControlledCapabilitySandbox:
    """Capture brokered execution while keeping the E2E side-effect free."""

    def __init__(self):
        self.calls = []

    def execute(self, command, *, workspace, environment=None):
        self.calls.append((tuple(command), workspace, environment))
        return SandboxResult(tuple(command), 0, "registered capability executed", "")


@pytest.mark.live
def test_live_gemini_director_executes_through_real_registered_capability(
    live_gemini_response,
    tmp_path,
):
    executable = tmp_path / "git"
    executable.write_text("#!/bin/sh\necho controlled\n", encoding="utf-8")

    capability_spec = ToolSpec(
        name="git",
        kind="system",
        source="ubuntu-apt",
        executable="git",
    )
    acquisition = ControlledCapabilityAcquisition(executable)
    capability_sandbox = ControlledCapabilitySandbox()
    capability_runtime = CapabilityRuntime(
        CapabilityBroker(
            Policy(
                frozenset({"tool:git"}),
                frozenset({"ubuntu-apt"}),
                require_verification=True,
            ),
            {"git": capability_spec},
            acquisition,
        ),
        capability_sandbox,
    )

    router = ControlledRouter()
    orchestration = OrchestrationEngine(
        router,
        capability_runtime=capability_runtime,
    )
    task_engine = TaskEngine(
        orchestration=orchestration,
        workspace_root=tmp_path,
    )
    planner = DirectorPlanner(
        provider=ReplayDirectorProvider(live_gemini_response),
        router=None,
    )
    service = DirectorService(planner, task_engine)

    result = service.run_mission(
        objective="Create exactly one research task to investigate Aurora architecture.",
    )

    assert result.plan.model
    assert len(result.plan.tasks) == 1
    task = result.plan.tasks[0]
    assert task.capability == "tool:git"

    assert len(result.tasks) == 1
    execution = result.tasks[0]
    assert execution.task == task
    assert execution.status is TaskStatus.SUCCEEDED
    assert execution.capability_result is not None
    assert execution.model_result is None

    capability_result = execution.capability_result
    assert capability_result.execution.returncode == 0
    assert capability_result.execution.stdout == "registered capability executed"
    assert capability_result.execution.capability.name == "tool:git"
    assert capability_result.execution.capability.verification == "verified"
    assert acquisition.calls == ["git"]
    assert len(capability_sandbox.calls) == 1
    assert capability_sandbox.calls[0][0] == (str(executable),)

    assert router.requests == []


class MissionReplayDirectorProvider(ReplayDirectorProvider):
    """Replay the live plan, then return a deterministic completion review."""

    def __init__(self, response: DirectorModelResponse):
        super().__init__(response)
        self.calls = 0
        self.review_contexts = []

    def generate(self, messages, model, role, tools=()):
        self.calls += 1
        if role == "director_planner":
            return self.response
        if role == "director_reviewer":
            return DirectorModelResponse(
                content='{"decision":"complete","rationale":"Controlled execution result satisfies the mission objective.","tasks":[]}',
                model=self.response.model,
                provider="gemini",
                request_id="replay-review",
                execution_id="replay-review",
                tool_calls=(),
            )
        raise AssertionError(f"unexpected Director role: {role}")


@pytest.mark.live
def test_live_gemini_director_closes_mission_after_execution_and_review(
    live_gemini_response,
    tmp_path,
):
    provider = MissionReplayDirectorProvider(live_gemini_response)
    planner = DirectorPlanner(provider=provider, router=None)

    executable = tmp_path / "git"
    executable.write_text("#!/bin/sh\\necho controlled\\n", encoding="utf-8")
    capability_spec = ToolSpec(
        name="git",
        kind="system",
        source="ubuntu-apt",
        executable="git",
    )
    acquisition = ControlledCapabilityAcquisition(executable)
    capability_sandbox = ControlledCapabilitySandbox()
    capability_runtime = CapabilityRuntime(
        CapabilityBroker(
            Policy(
                frozenset({"tool:git"}),
                frozenset({"ubuntu-apt"}),
                require_verification=True,
            ),
            {"git": capability_spec},
            acquisition,
        ),
        capability_sandbox,
    )
    router = ControlledRouter()
    orchestration = OrchestrationEngine(
        router,
        capability_runtime=capability_runtime,
    )
    task_engine = TaskEngine(
        orchestration=orchestration,
        workspace_root=tmp_path / "workspace",
    )
    store = DurableTaskStore(tmp_path / "tasks.db")
    event_bus = DurableEventBus(store)
    durable_director = DurableDirectorService(
        planner=planner,
        task_engine=task_engine,
        store=store,
        event_bus=event_bus,
    )
    runtime = DirectorRuntime(
        director=durable_director,
        store=store,
        event_bus=event_bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=2,
            idle_sleep_seconds=0,
        ),
        worker_id="live-gemini-director",
        event_consumer_id="live-gemini-director-events",
        worker_concurrency=1,
        max_review_rounds=1,
    )

    submission = runtime.submit_mission(
        objective="Create exactly one research task to investigate Aurora architecture.",
    )
    result = runtime.run_until_terminal(submission, max_cycles=20)

    assert result.status is TaskStatus.SUCCEEDED
    assert len(result.tasks) == 1
    assert result.tasks[0].status is TaskStatus.SUCCEEDED
    assert provider.calls == 2
    assert len(router.requests) == 0

    events = [event.event_type for event in store.events(submission.mission_id)]
    assert "mission_submitted" in events
    assert "worker.task_succeeded" in events
    assert "director_review_started" in events
    assert "director_review" in events
    assert "mission_succeeded" in events


class ReplanningDirectorProvider(MissionReplayDirectorProvider):
    """Replay the live plan, then force one review-driven follow-up phase."""

    def generate(self, messages, model, role, tools=()):
        self.calls += 1
        if role == "director_planner":
            return self.response
        if role == "director_reviewer":
            self.review_contexts.append(tuple(messages))
            if self.calls == 2:
                return DirectorModelResponse(
                    content=(
                        '{"decision":"continue","rationale":"The first execution completed, '
                        'but a second controlled validation step is required.","tasks":['
                        '{"task_id":"validate_result","role":"engineer",'
                        '"objective":"Validate the controlled execution result.",'
                        '"capability":null,"args":[],"depends_on":[],"skills":[]}]}'
                    ),
                    model=self.response.model,
                    provider="gemini",
                    request_id="replay-review-1",
                    execution_id="replay-review-1",
                    tool_calls=(),
                )
            if self.calls == 3:
                return DirectorModelResponse(
                    content=(
                        '{"decision":"complete","rationale":"The follow-up validation completed '
                        'and the mission objective is now satisfied.","tasks":[]}'
                    ),
                    model=self.response.model,
                    provider="gemini",
                    request_id="replay-review-2",
                    execution_id="replay-review-2",
                    tool_calls=(),
                )
        raise AssertionError(f"unexpected Director role/call: {role}/{self.calls}")


@pytest.mark.live
def test_live_gemini_director_replans_after_review_and_completes(
    live_gemini_response,
    tmp_path,
):
    provider = ReplanningDirectorProvider(live_gemini_response)
    planner = DirectorPlanner(provider=provider, router=None)
    router = ControlledRouter()
    skill = SkillSpec(
        skill_id="research.validation",
        version="1.0.0",
        name="Research validation",
        mission="Validate research execution outputs.",
        instructions="Return governed verification and quality markers.",
        required_capabilities=frozenset(),
        verification_requirements=("result_present",),
        quality_checks=("quality_ok",),
        tags=("research", "verification"),
        model_preferences=("controlled-task-model",),
    )
    skill_registry = SkillRegistry({"research.validation": skill})
    organization = OrganizationRegistry(
        agents={
            "agent-engineer": AgentSpec(
                agent_id="agent-engineer",
                role="engineer",
                department="engineering",
                mission="Perform controlled repository research validation.",
                capabilities=frozenset({"tool:git"}),
                model_preferences=("controlled-task-model",),
                skill_ids=("research.validation",),
            )
        },
        departments={
            "engineering": DepartmentSpec(
                department_id="engineering",
                mission="Repository research validation",
                agent_ids=("agent-engineer",),
                model_preferences=("controlled-task-model",),
                allowed_capabilities=frozenset({"tool:git"}),
            )
        },
    )
    skill_learning = SkillLearningStore(tmp_path / "skills.db")
    model_performance = ModelPerformanceStore(tmp_path / "model-performance.db")
    task_engine = TaskEngine(
        orchestration=OrchestrationEngine(router),
        workspace_root=tmp_path / "workspace",
        organization=organization,
        skill_registry=skill_registry,
        skill_learning=skill_learning,
        model_performance=model_performance,
    )
    store = DurableTaskStore(tmp_path / "tasks.db")
    event_bus = DurableEventBus(store)
    durable_director = DurableDirectorService(
        planner=planner,
        task_engine=task_engine,
        store=store,
        event_bus=event_bus,
    )
    runtime = DirectorRuntime(
        director=durable_director,
        store=store,
        event_bus=event_bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=2,
            idle_sleep_seconds=0,
        ),
        worker_id="live-gemini-replanner",
        event_consumer_id="live-gemini-replanner-events",
        worker_concurrency=1,
        max_review_rounds=2,
    )

    submission = runtime.submit_mission(
        objective="Create exactly one research task to investigate Aurora architecture.",
    )
    result = runtime.run_until_terminal(submission, max_cycles=30)

    assert result.status is TaskStatus.SUCCEEDED
    assert len(result.tasks) == 2
    assert result.tasks[0].status is TaskStatus.SUCCEEDED
    assert result.tasks[1].status is TaskStatus.SUCCEEDED
    assert result.tasks[1].task.task_id == "r1_validate_result"

    assert provider.calls == 3
    assert len(router.requests) == 2
    scores = model_performance.trusted_scores_for(
        skill_ids=("research.validation",),
        skill_versions={"research.validation": "1.0.0"},
        task_type="research",
        context_bucket="small",
        minimum_evaluations=1,
        minimum_evidence_completeness=1.0,
    )
    assert scores == {"controlled-task-model": 1.0}
    assert provider.review_contexts
    assert any(
        "Observed specialist evaluations:" in message.get("content", "")
        and "score=1.0" in message.get("content", "")
        and "evidence_state=evidence_complete" in message.get("content", "")
        for review in provider.review_contexts
        for message in review
    )

    events = [event.event_type for event in store.events(submission.mission_id)]
    assert events.count("director_review") == 2
    assert "mission_reopened" in events
    assert "worker.task_succeeded" in events
    assert events[-1] == "mission_succeeded"
