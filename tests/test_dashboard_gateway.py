import json
from pathlib import Path

from aurora.dashboard.gateway import DashboardGateway, DashboardGatewayError
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.inference.models import ModelSpec
from aurora.inference.providers.registry import ProviderRegistry
from aurora.inference.registry import ModelRegistry
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.mission_memory import MailboxScope, MissionMemory
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_engine import DelegatedTask
from aurora.projects import ProjectRegistry, ProjectSpec, ProjectStatus
from aurora.research.store import ResearchStore
from aurora.skills import SkillLearningStore, SkillRegistry
from aurora.skills.models import SkillSpec


def _gateway(tmp_path: Path, *, include_project: bool = True) -> DashboardGateway:
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher",
                "researcher",
                "research",
                "Research",
                frozenset({"reasoning"}),
                ("model/research",),
                ("research",),
            )
        },
        departments={
            "research": DepartmentSpec(
                "research",
                "Research",
                ("researcher",),
                ("model/research",),
                frozenset({"reasoning"}),
            )
        },
    )
    skill_registry = SkillRegistry(
        {
            "research": SkillSpec(
                "research",
                "1.0.0",
                "Research",
                "Research mission",
                "Use verified evidence.",
                frozenset({"reasoning"}),
                ("source_check",),
                ("citation_check",),
                ("research",),
            )
        }
    )
    models = (
        ModelSpec(
            "model/research",
            "nvidia_api",
            "https://example.invalid",
            capabilities=("reasoning",),
            specialties=("research",),
            context_window=8192,
        ),
    )
    provider_registry = ProviderRegistry()
    provider_registry.register(
        __import__("aurora.inference.providers.registry", fromlist=["ProviderSpec"]).ProviderSpec(
            "nvidia_api",
            "NVIDIA test",
            ("chat", "reasoning"),
            True,
            lambda model: object(),
        )
    )
    model_registry = ModelRegistry(models)
    projects = (
        ProjectRegistry(
            {
                "project-a": ProjectSpec(
                    "project-a",
                    "Project A",
                    "Research objective",
                    ProjectStatus.ACTIVE,
                    repository="https://github.com/example/project-a",
                    workspace="/tmp/project-a",
                    architecture_ref="docs/architecture.md",
                    agent_roles=("researcher",),
                    skill_ids=("research",),
                    model_ids=("model/research",),
                    task_ids=("task-1",),
                    current_state="running",
                    updated_at="2026-10-01T13:00:00+00:00",
                )
            }
        )
        if include_project
        else ProjectRegistry({})
    )
    task_store = DurableTaskStore(tmp_path / "tasks.db")
    task_store.enqueue(
        "mission-1",
        (
            DelegatedTask(
                task_id="task-1",
                role="researcher",
                objective="Collect evidence",
                skills=("research",),
            ),
        ),
    )
    task_store.append_event(
        mission_id="mission-1",
        task_id="task-1",
        event_type="created",
        payload={"ok": True},
        project_id="project-a" if include_project else None,
        agent_role="researcher",
        correlation_id="corr-1",
    )
    memory = MissionMemory(task_store)
    memory.append(
        mission_id="mission-1",
        source="director",
        kind="notice",
        content="Project notice",
        scope=MailboxScope.PROJECT,
        project_id="project-a" if include_project else None,
        agent_role="researcher",
        task_id="task-1",
        correlation_id="corr-1",
    )
    return DashboardGateway(
        project_registry=projects,
        organization=organization,
        skill_registry=skill_registry,
        model_registry=model_registry,
        provider_registry=provider_registry,
        task_store=task_store,
        mission_memory=memory,
        model_performance=ModelPerformanceStore(tmp_path / "model-performance.db"),
        skill_learning=SkillLearningStore(tmp_path / "skill-learning.db"),
        research_store=ResearchStore(tmp_path / "research.db"),
    )


def test_dashboard_snapshot_is_read_only_and_json_serializable(tmp_path: Path):
    gateway = _gateway(tmp_path)
    snapshot = gateway.snapshot()

    assert snapshot["read_only"] is True
    assert snapshot["snapshot_id"].startswith("dash-")
    assert snapshot["projects"][0]["project_id"] == "project-a"
    assert snapshot["models"][0]["model_id"] == "model/research"
    assert snapshot["providers"][0]["provider_id"] == "nvidia_api"
    assert snapshot["mailbox"]["messages"][0]["correlation_id"] == "corr-1"
    json.dumps(snapshot)


def test_dashboard_project_and_agent_inspectors_are_authoritative(tmp_path: Path):
    gateway = _gateway(tmp_path)

    project = gateway.project("project-a")
    assert project["project"]["objective"] == "Research objective"
    assert project["agents"][0]["role"] == "researcher"
    assert project["tasks"][0]["task_id"] == "task-1"
    assert project["events"][0]["project_id"] == "project-a"

    agent = gateway.agent("researcher")
    assert agent["identity"]["department"] == "research"
    assert agent["projects"][0]["project_id"] == "project-a"
    assert agent["current_tasks"]


def test_dashboard_mailbox_supports_project_and_correlation_filters(tmp_path: Path):
    gateway = _gateway(tmp_path)

    by_project = gateway.mailbox(project_id="project-a")
    by_correlation = gateway.mailbox(correlation_id="corr-1")

    assert by_project["events"][0]["project_id"] == "project-a"
    assert by_correlation["events"][0]["correlation_id"] == "corr-1"


def test_dashboard_returns_unknown_for_unconfigured_research(tmp_path: Path):
    gateway = _gateway(tmp_path)
    gateway.research_store = None

    result = gateway.research()
    assert result["state"] == "unknown"


def test_dashboard_project_resolution_is_fail_closed(tmp_path: Path):
    gateway = _gateway(tmp_path)

    try:
        gateway.project("missing")
    except Exception as exc:
        assert "unknown Aurora project" in str(exc)
    else:
        raise AssertionError("expected unknown project to fail")


def test_dashboard_limits_are_bounded(tmp_path: Path):
    gateway = _gateway(tmp_path)

    try:
        gateway.snapshot(event_limit=1001)
    except DashboardGatewayError as exc:
        assert "between 1 and 1000" in str(exc)
    else:
        raise AssertionError("expected oversized snapshot request to fail")



def test_dashboard_model_activity_is_unknown_without_router_and_live_with_router(tmp_path: Path):
    gateway = _gateway(tmp_path)

    missing = gateway.snapshot()["model_activity"]
    assert missing["state"] == "unknown"
    assert missing["active_executions"] == []

    class ActivityRouter:
        def active_executions(self):
            return ({
                "execution_id": "execution-1",
                "request_id": "request-1",
                "model_id": "model/research",
                "provider": "nvidia_api",
                "role": "researcher",
                "task_id": "task-1",
                "mission_id": "mission-1",
                "project_id": "project-a",
                "task_type": "research",
                "started_at": "2026-10-12T10:00:00+00:00",
                "status": "running",
                "prompt": "must never be exposed",
            },)

    gateway.model_router = ActivityRouter()
    activity = gateway.snapshot()["model_activity"]
    assert activity["state"] == "authoritative"
    assert activity["active_count"] == 1
    assert activity["active_executions"][0]["model_id"] == "model/research"
    assert "prompt" not in activity["active_executions"][0]
