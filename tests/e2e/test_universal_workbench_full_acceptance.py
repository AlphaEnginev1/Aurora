"""Full Universal Workbench acceptance mission.

This controlled E2E proves the architectural completion contract without requiring
live credentials. External live gates remain responsible for real provider/network
verification.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from aurora.autonomy.capability_resolution import CapabilityResolution
from aurora.autonomy.internet import InternetPolicy, InternetResponse
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
from aurora.research.discovery import DiscoveredSource
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore
from aurora.workbench import UniversalWorkbench, WorkbenchResult, WorkSessionStore


class Gateway:
    timeout = 1.0

    def __init__(self):
        self.policy = InternetPolicy(
            frozenset({"https"}),
            frozenset({"example.com", "api.github.com"}),
        )
        self.urls: list[str] = []

    def get(self, url: str) -> bytes:
        self.urls.append(url)
        return (
            b"<html><body><p>Controlled research evidence from Aurora.</p>"
            b"<p>Claims remain pending until independently verified.</p></body></html>"
        )

    def get_with_metadata(self, url: str) -> InternetResponse:
        self.urls.append(url)
        return InternetResponse(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body=(
                b"<html><body><p>Controlled research evidence from Aurora.</p>"
                b"<p>Claims remain pending until independently verified.</p></body></html>"
            ),
        )


class Discovery:
    def search(self, objective: str, *, limit: int):
        return (
            DiscoveredSource("https://example.com/research-a", "A"),
            DiscoveredSource("https://example.com/research-b", "B"),
        )[:limit]


class Resolution:
    def __init__(self):
        self.calls = []

    def ensure(self, request: CapabilityRequest) -> CapabilityResolution:
        self.calls.append(request)
        result = CapabilityResult(
            name=request.name,
            status="installed",
            version="1.0.0",
            source="controlled",
            verification="verified",
            path="/managed/controlled-tool",
            sha256="c" * 64,
            artifact_id="artifact-resolved",
        )
        return CapabilityResolution(
            request.name,
            result,
            discovered=("controlled-source",),
            attempts=2,
        )


class CapabilityRuntime:
    def __init__(self):
        self.calls = []

        class Broker:
            @staticmethod
            def resolve(request):
                return object() if request.name == "tool:python" else None

        self.broker = Broker()

    def execute(self, request, *, workspace, args=(), environment=None):
        self.calls.append((request, workspace, tuple(args), dict(environment or {})))
        capability = SimpleNamespace(
            artifact_id="artifact-python",
            name=request.name,
        )
        return SimpleNamespace(
            capability=capability,
            command=("/workspace/python", *args),
            returncode=0,
            stdout="VALIDATION_OK",
            stderr="",
            timed_out=False,
        )


class GitHub:
    write_enabled = True
    merge_enabled = False

    def __init__(self):
        self.actions = []

    def available(self):
        return True

    def get(self, target):
        self.actions.append(("get", target))
        return {
            "name": "controlled-repo",
            "html_url": "https://github.com/controlled/controlled-repo",
            "default_branch": "main",
        }

    def create_branch(self, owner, repo, branch, sha):
        self.actions.append(("branch", owner, repo, branch, sha))
        return {"ref": "refs/heads/" + branch, "object": {"sha": sha}}

    def upsert_file(self, owner, repo, path, content, message, branch, sha=None):
        self.actions.append(("file_write", owner, repo, path, branch, content))
        return {"content": {"path": path}, "commit": {"sha": "commit-controlled"}}

    def create_pull_request(self, owner, repo, title, head, base, body="", draft=False):
        self.actions.append(("pr", owner, repo, head, base))
        return {"number": 77, "html_url": "https://github.com/controlled/controlled-repo/pull/77", "state": "open"}

    def workflow_runs(self, owner, repo, *, branch=None, status=None):
        return {"workflow_runs": [{"id": 1, "status": "completed", "conclusion": "success"}]}

    @staticmethod
    def repo_path(owner, repo, suffix=""):
        return f"/repos/{owner}/{repo}{suffix}"


class Router(ModelRouter):
    def __init__(self):
        super().__init__([
            ModelSpec(
                "acceptance/multimodal",
                "controlled",
                "https://example.invalid",
                capabilities=("reasoning", "image"),
            )
        ])
        self.requests: list[InferenceRequest] = []
        self.counters = {"research-specialist": 0, "engineering-specialist": 0}
        self.lock = threading.Lock()

    def generate(self, request, *, capability="reasoning", preferred_models=None):
        with self.lock:
            self.requests.append(request)
            task_id = str(request.metadata.get("task_id", ""))
            self.counters[task_id] = self.counters.get(task_id, 0) + 1
            step = self.counters[task_id]

        decision = self.route(request, capability=capability, preferred_models=preferred_models)
        calls = {
            "research-specialist": {
                1: ("workbench:project:inspect", ["."], {}),
                2: ("workbench:project:register", [
                    json.dumps({
                        "project_id": "acceptance-local",
                        "name": "Controlled Acceptance Project",
                        "objective": "Prove the Universal Workbench",
                        "workspace": str(request.metadata.get("workspace_root", ".")),
                    })
                ], {}),
                3: ("workbench:research:discover", [
                    json.dumps({
                        "request_id": "research-discover",
                        "objective": "Find controlled authoritative evidence",
                        "max_sources": 2,
                    })
                ], {}),
                4: ("workbench:research:retrieve_extract", [
                    json.dumps({
                        "request_id": "research-retrieve",
                        "objective": "Retrieve controlled authoritative evidence",
                        "required_sources": ["https://example.com/research-a"],
                        "max_sources": 1,
                    })
                ], {}),
                5: ("workbench:document:extract", ["brief.docx"], {"max_chars": 10000}),
                6: ("workbench:computer", ['[{"action":"screenshot"}]'], {}),
                7: ("workbench:browser", [
                    json.dumps([{
                        "action": "navigate",
                        "url": "https://example.com/research-a",
                    }])
                ], {}),
                8: ("workbench:capability:resolve", [
                    json.dumps({
                        "name": "tool.acceptance:demo",
                        "constraints": {"source": "controlled"},
                    })
                ], {}),
            },
            "engineering-specialist": {
                1: ("workbench:file:read", ["notes.txt"], {}),
                2: ("workbench:artifact:intake", ["notes.txt"], {}),
                3: ("workbench:file:write", ["result.txt", "IMPLEMENTED"], {}),
                4: ("workbench:command", [
                    json.dumps({
                        "capability": "tool:python",
                        "args": ["-c", "print('validation ok')"],
                    })
                ], {}),
                5: ("workbench:github:get", ["repo:controlled/controlled-repo"], {}),
                6: ("workbench:github:branch", [
                    json.dumps({
                        "owner": "controlled",
                        "repo": "controlled-repo",
                        "branch": "acceptance/result",
                        "sha": "base-sha",
                    })
                ], {}),
                7: ("workbench:github:file:write", [
                    json.dumps({
                        "owner": "controlled",
                        "repo": "controlled-repo",
                        "path": "result.txt",
                        "content": "IMPLEMENTED",
                        "message": "Acceptance result",
                        "branch": "acceptance/result",
                    })
                ], {}),
                8: ("workbench:github:pr", [
                    json.dumps({
                        "owner": "controlled",
                        "repo": "controlled-repo",
                        "title": "Universal Workbench acceptance",
                        "head": "acceptance/result",
                        "base": "main",
                        "body": "Controlled acceptance evidence.",
                    })
                ], {}),
            },
        }

        if task_id in calls and step <= max(calls[task_id]):
            capability_name, args, options = calls[task_id][step]
            content = json.dumps({
                "workbench_calls": [{
                    "capability": capability_name,
                    "arguments": args,
                    "options": options,
                }]
            })
        else:
            content = f"{task_id} completed using authoritative Workbench evidence."

        return InferenceResponse(
            model=decision.selected_model,
            provider="controlled",
            content=content,
            request_id=request.request_id,
            execution_id=f"acceptance-{task_id}-{step}",
            selected_model=decision.selected_model,
            routing_score=decision.selected_score,
            routing_factors=decision.factors,
            routing_candidates=decision.candidates,
        )


class DirectorProvider:
    provider_id = "controlled"

    def __init__(self):
        self.reviews = 0

    def generate(self, messages, model, role, tools=()):
        if role == "director_planner":
            return DirectorModelResponse(
                content=json.dumps({
                    "tasks": [
                        {
                            "task_id": "research-specialist",
                            "role": "researcher",
                            "objective": "Perform project intake, research, document and multimodal inspection, then resolve one missing capability.",
                            "capability": None,
                            "args": [],
                            "depends_on": [],
                            "skills": [],
                        },
                        {
                            "task_id": "engineering-specialist",
                            "role": "engineer",
                            "objective": "Inspect, modify and validate the project, then perform the governed GitHub branch/file/PR workflow.",
                            "capability": None,
                            "args": [],
                            "depends_on": [],
                            "skills": [],
                        },
                    ]
                }),
                model="controlled/director",
                provider="controlled",
                request_id="planner-1",
                execution_id="planner-execution-1",
            )
        if role == "director_reviewer":
            self.reviews += 1
            return DirectorModelResponse(
                content=json.dumps({
                    "decision": "complete",
                    "rationale": "Both specialists completed the full governed Universal Workbench path.",
                    "tasks": [],
                }),
                model="controlled/director",
                provider="controlled",
                request_id=f"review-{self.reviews}",
                execution_id=f"review-execution-{self.reviews}",
            )
        raise AssertionError(f"unexpected role: {role}")


def _docx(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    import zipfile
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Controlled document evidence.</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def test_full_universal_workbench_acceptance_mission(tmp_path: Path):
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / "notes.txt").write_text("Controlled acceptance notes.", encoding="utf-8")
    _docx(repo / "brief.docx")
    (repo / "pyproject.toml").write_text("[project]\\nname='acceptance-project'\\n", encoding="utf-8")

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    gateway = Gateway()
    research = ResearchService(
        gateway,
        ResearchStore(runtime_root / "research.db"),
        discovery=Discovery(),
    )
    resolution = Resolution()
    capability_runtime = CapabilityRuntime()
    github = GitHub()
    sessions = WorkSessionStore(runtime_root / "work-sessions.db")

    workbench = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime_root,
        gateway=gateway,
        policy=Policy(frozenset({"workbench:*", "tool:*", "python.package:*"}), frozenset()),
        session_store=sessions,
        audit_path=runtime_root / "audit.jsonl",
        resolution=resolution,
        research=research,
        capability_runtime=capability_runtime,
        configured_roots=(repo,),
    )
    workbench.github = github
    workbench._browser_run = lambda request: WorkbenchResult(
        request.capability,
        "browser_acceptance",
        "succeeded",
        output={"url": "https://example.com/research-a", "title": "Controlled Research"},
        provenance="controlled:browser",
    )
    workbench._computer_action = lambda request: WorkbenchResult(
        request.capability,
        "computer_screenshot",
        "succeeded",
        output=[{"mime_type": "image/png", "data": "ZmFrZS1pbWFnZQ=="}],
        artifact_id="artifact-screenshot",
        provenance="controlled:computer",
    )

    router = Router()
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher", "researcher", "research",
                "Research specialist", frozenset(), ("acceptance/multimodal",), ()
            ),
            "engineer": AgentSpec(
                "engineer", "engineer", "engineering",
                "Engineering specialist", frozenset(), ("acceptance/multimodal",), ()
            ),
        },
        departments={
            "research": DepartmentSpec(
                "research", "Research", ("researcher",),
                ("acceptance/multimodal",), frozenset()
            ),
            "engineering": DepartmentSpec(
                "engineering", "Engineering", ("engineer",),
                ("acceptance/multimodal",), frozenset()
            ),
        },
    )
    orchestration = OrchestrationEngine(router, workbench=workbench, capability_runtime=capability_runtime)
    task_engine = TaskEngine(orchestration, repo, organization=organization)

    store = DurableTaskStore(runtime_root / "missions.db")
    event_bus = DurableEventBus(store)
    planner = DirectorPlanner(
        provider=DirectorProvider(),
        router=router,
        organization=organization,
    )
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
        worker_policy=WorkerPolicy(lease_seconds=30, max_attempts=2, idle_sleep_seconds=0),
        worker_id="full-acceptance-worker",
        event_consumer_id="full-acceptance-events",
        worker_concurrency=2,
        max_review_rounds=1,
    )

    submission = runtime.submit_mission(
        objective="Run the full Universal Workbench acceptance mission."
    )
    result = runtime.run_until_terminal(submission, max_cycles=24)

    assert result.status is TaskStatus.SUCCEEDED
    assert {item.task.task_id for item in result.tasks} == {
        "research-specialist",
        "engineering-specialist",
    }
    failed_tasks = [
        {
            "task_id": item.task.task_id,
            "status": item.status.value,
            "error": item.error,
            "capability": item.task.capability,
        }
        for item in result.tasks
        if item.status is not TaskStatus.SUCCEEDED
    ]
    assert not failed_tasks, f"Universal Workbench specialist failures: {failed_tasks}"

    session = sessions.get(submission.mission_id)
    required_caps = {
        "workbench:project:inspect",
        "workbench:project:register",
        "workbench:research:discover",
        "workbench:research:retrieve_extract",
        "workbench:document:extract",
        "workbench:computer",
        "workbench:browser",
        "workbench:capability:resolve",
        "workbench:file:read",
        "workbench:artifact:intake",
        "workbench:file:write",
        "workbench:command",
        "workbench:github:get",
        "workbench:github:branch",
        "workbench:github:file:write",
        "workbench:github:pr",
    }
    missing_capabilities = required_caps - set(session.capability_ids)
    assert not missing_capabilities, (
        f"missing Workbench capabilities: {sorted(missing_capabilities)}; "
        f"observed={sorted(set(session.capability_ids))}; counters={router.counters}"
    )
    assert len(session.execution_receipts) == 16
    assert "artifact-screenshot" in session.artifact_ids
    assert "artifact-resolved" in session.artifact_ids
    assert "artifact-python" in session.artifact_ids
    assert (repo / "result.txt").read_text(encoding="utf-8") == "IMPLEMENTED"

    assert gateway.urls == ["https://example.com/research-a"]
    assert resolution.calls[0].name == "tool.acceptance:demo"
    assert resolution.calls[0].actor == "researcher"
    assert capability_runtime.calls[0][0].name == "tool:python"
    assert capability_runtime.calls[0][0].actor == "engineer"
    assert [item[0] for item in github.actions] == [
        "get", "branch", "file_write", "pr"
    ]

    project_graph = json.loads(
        (runtime_root / "project-source-graph.json").read_text(encoding="utf-8")
    )
    assert any(
        item["project_id"] == "acceptance-local" and item["source_type"] == "local"
        for item in project_graph["sources"]
    )

    audit = (runtime_root / "audit.jsonl").read_text(encoding="utf-8")
    assert "receipt_id" in audit
    assert "correlation_id" in audit
    assert "aurora-research:retrieval" in audit
    assert "aurora-capability-resolution" in audit
    assert "github-api" in audit

    reopened_sessions = WorkSessionStore(runtime_root / "work-sessions.db")
    restored = reopened_sessions.get(submission.mission_id)
    assert restored.execution_receipts == session.execution_receipts
    assert restored.correlation_ids == session.correlation_ids

    reopened_store = DurableTaskStore(runtime_root / "missions.db")
    assert {
        event.event_type for event in reopened_store.events(submission.mission_id)
    }.issuperset({
        "mission_submitted",
        "worker.task_succeeded",
        "director_review",
        "mission_succeeded",
    })
    restarted_runtime = DirectorRuntime(
        director=durable,
        store=reopened_store,
        event_bus=DurableEventBus(reopened_store),
        worker_policy=WorkerPolicy(lease_seconds=30, max_attempts=2, idle_sleep_seconds=0),
        worker_id="full-acceptance-restarted-worker",
        event_consumer_id="full-acceptance-restarted-events",
        worker_concurrency=2,
        max_review_rounds=1,
    )
    restarted = restarted_runtime.run_once()
    assert restarted.did_work is False
