"""Universal Workbench acceptance mission.

One deterministic mission proves the real orchestration path:
Director planning -> specialist reasoning -> shared Universal Workbench ->
preflight/evidence/media/capability resolution -> durable receipts -> review ->
mission completion.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from aurora.autonomy.capability_resolution import CapabilityResolution
from aurora.autonomy.internet import InternetPolicy
from aurora.autonomy.models import CapabilityRequest, CapabilityResult
from aurora.autonomy.policy import Policy
from aurora.inference.models import InferenceRequest, InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.organization.registry import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director import DirectorPlanner
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import OrchestrationEngine
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_engine import TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy
from aurora.workbench import UniversalWorkbench, WorkbenchResult, WorkSessionStore


class AcceptanceGateway:
    policy = InternetPolicy(
        frozenset({"https"}),
        frozenset({"example.com", "api.github.com"}),
    )


class AcceptanceResolution:
    def __init__(self):
        self.calls: list[CapabilityRequest] = []

    def ensure(self, request: CapabilityRequest) -> CapabilityResolution:
        self.calls.append(request)
        result = CapabilityResult(
            name=request.name,
            status="installed",
            version="1.0.0",
            source="controlled",
            verification="verified",
            path="/managed-site/controlled-tool",
            sha256="b" * 64,
            artifact_id="artifact-capability-1",
        )
        return CapabilityResolution(
            request.name,
            result,
            discovered=("controlled-source",),
            attempts=2,
        )


class AcceptanceRouter(ModelRouter):
    def __init__(self):
        super().__init__([
            ModelSpec(
                "acceptance/vision-reasoner",
                "controlled",
                "https://example.invalid",
                capabilities=("reasoning", "image"),
            )
        ])
        self.requests: list[InferenceRequest] = []
        self.step = 0

    def generate(self, request, *, capability="reasoning", preferred_models=None):
        self.requests.append(request)
        self.step += 1
        decision = self.route(
            request,
            capability=capability,
            preferred_models=preferred_models,
        )
        if self.step == 1:
            content = json.dumps({
                "workbench_calls": [{
                    "capability": "workbench:preflight",
                    "arguments": [],
                    "options": {},
                }]
            })
        elif self.step == 2:
            content = json.dumps({
                "workbench_calls": [{
                    "capability": "workbench:file:read",
                    "arguments": ["notes.txt"],
                    "options": {},
                }]
            })
        elif self.step == 3:
            content = json.dumps({
                "workbench_calls": [{
                    "capability": "workbench:computer",
                    "arguments": ['[{"action":"screenshot"}]'],
                    "options": {},
                }]
            })
        elif self.step == 4:
            content = json.dumps({
                "workbench_calls": [{
                    "capability": "workbench:capability:resolve",
                    "arguments": [json.dumps({
                        "name": "tool.acceptance:demo",
                        "constraints": {"source": "controlled"},
                    })],
                    "options": {},
                }]
            })
        else:
            content = (
                "Specialist completed using governed Workbench evidence: "
                "preflight, file evidence, media evidence and capability resolution."
            )
        return InferenceResponse(
            model=decision.selected_model,
            provider="controlled",
            content=content,
            request_id=request.request_id,
            execution_id=f"acceptance-execution-{self.step}",
            selected_model=decision.selected_model,
            routing_score=decision.selected_score,
            routing_factors=decision.factors,
            routing_candidates=decision.candidates,
        )


class AcceptanceDirectorProvider:
    provider_id = "controlled"

    def __init__(self):
        self.plans = 0
        self.reviews = 0
        self.review_contexts: list[tuple[dict[str, object], ...]] = []

    def generate(self, messages, model, role, tools=()):
        if role == "director_planner":
            self.plans += 1
            return DirectorModelResponse(
                content=(
                    '{"tasks":[{"task_id":"universal_specialist",'
                    '"role":"researcher",'
                    '"objective":"Use the Universal Workbench to inspect project evidence, '
                    'verify operational readiness, inspect multimodal evidence and resolve '
                    'one governed capability.",'
                    '"capability":null,"args":[],"depends_on":[],"skills":[]}]}'
                ),
                model="controlled/director",
                provider="controlled",
                request_id=f"planner-{self.plans}",
                execution_id=f"planner-execution-{self.plans}",
            )
        if role == "director_reviewer":
            self.reviews += 1
            self.review_contexts.append(tuple(messages))
            return DirectorModelResponse(
                content=(
                    '{"decision":"complete",'
                    '"rationale":"The specialist completed the full governed Workbench path.",'
                    '"tasks":[]}'
                ),
                model="controlled/director",
                provider="controlled",
                request_id=f"review-{self.reviews}",
                execution_id=f"review-execution-{self.reviews}",
            )
        raise AssertionError(f"unexpected Director role: {role}")


def test_universal_workbench_acceptance_mission(tmp_path: Path):
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / "notes.txt").write_text(
        "Universal Workbench acceptance evidence.",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    resolution = AcceptanceResolution()
    sessions = WorkSessionStore(runtime_root / "work-sessions.db")
    workbench = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime_root,
        gateway=AcceptanceGateway(),
        policy=Policy(
            frozenset({"workbench:*"}),
            frozenset(),
        ),
        session_store=sessions,
        audit_path=runtime_root / "audit.jsonl",
        resolution=resolution,
        configured_roots=(repo,),
    )
    workbench._computer_action = lambda request: WorkbenchResult(
        request.capability,
        "computer_screenshot",
        "succeeded",
        output=[{
            "mime_type": "image/png",
            "data": "ZmFrZS1hY2NlcHRhbmNlLXNjcmVlbnNob3Q=",
        }],
        artifact_id="artifact-screen-1",
        provenance="controlled:computer",
    )

    router = AcceptanceRouter()
    orchestration = OrchestrationEngine(router, workbench=workbench)

    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher",
                "researcher",
                "research",
                "Universal Workbench acceptance specialist.",
                frozenset(),
                ("acceptance/vision-reasoner",),
                (),
            )
        },
        departments={
            "research": DepartmentSpec(
                "research",
                "Research",
                ("researcher",),
                ("acceptance/vision-reasoner",),
                frozenset(),
            )
        },
    )
    task_engine = TaskEngine(
        orchestration,
        repo,
        organization=organization,
    )

    director_provider = AcceptanceDirectorProvider()
    planner = DirectorPlanner(
        provider=director_provider,
        router=None,
        organization=organization,
    )
    store = DurableTaskStore(runtime_root / "missions.db")
    event_bus = DurableEventBus(store)
    durable = DurableDirectorService(
        planner=planner,
        task_engine=task_engine,
        store=store,
        event_bus=event_bus,
    )
    runtime = DirectorRuntime(
        director=durable,
        store=store,
        event_bus=event_bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=2,
            idle_sleep_seconds=0,
        ),
        worker_id="universal-workbench-acceptance",
        event_consumer_id="universal-workbench-acceptance-events",
        worker_concurrency=1,
        max_review_rounds=1,
    )

    submission = runtime.submit_mission(
        objective="Prove that Aurora's Director and specialist share one governed Workbench."
    )
    result = runtime.run_until_terminal(submission, max_cycles=12)

    assert result.status is TaskStatus.SUCCEEDED
    assert [item.task.task_id for item in result.tasks] == ["universal_specialist"]
    assert result.tasks[0].status is TaskStatus.SUCCEEDED

    session = sessions.get(submission.mission_id)
    assert session.capability_ids[:4] == (
        "workbench:preflight",
        "workbench:file:read",
        "workbench:computer",
        "workbench:capability:resolve",
    )
    assert len(session.execution_receipts) == 4
    assert "artifact-capability-1" in session.artifact_ids
    assert "artifact-screen-1" in session.artifact_ids
    assert len(sessions.get_receipts(submission.mission_id)) == 4

    assert resolution.calls
    assert resolution.calls[0].name == "tool.acceptance:demo"
    assert resolution.calls[0].actor == "researcher"
    assert resolution.calls[0].constraints["source"] == "controlled"

    assert any(
        message.get("role") == "user"
        and isinstance(message.get("content"), list)
        and any(
            part.get("modality") == "image"
            for part in message["content"]
            if isinstance(part, dict)
        )
        for request in router.requests
        for message in request.messages
    )
    assert any(
        "preflight" in str(message.get("content", ""))
        or "capability_resolution" in str(message.get("content", ""))
        for request in router.requests
        for message in request.messages
    )

    events = {event.event_type for event in store.events(submission.mission_id)}
    assert {
        "mission_submitted",
        "worker.task_succeeded",
        "director_review",
        "mission_succeeded",
    }.issubset(events)

    audit = (runtime_root / "audit.jsonl").read_text(encoding="utf-8")
    assert "receipt_id" in audit
    assert "correlation_id" in audit
    assert "notes.txt" not in audit
