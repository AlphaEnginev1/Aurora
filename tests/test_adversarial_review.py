from pathlib import Path

from aurora.inference.models import InferenceResponse
from aurora.organization import AgentSelector, AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.team_runtime import TeamCoordinator, TeamCoordinatorError
from aurora.orchestration.task_worker import WorkerPolicy


def organization():
    return OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research evidence",
                capabilities=frozenset(),
                model_preferences=(),
                skill_ids=("research",),
            ),
            "reviewer": AgentSpec(
                agent_id="reviewer",
                role="reviewer",
                department="verification",
                mission="verify evidence and find contradictions",
                capabilities=frozenset(),
                model_preferences=(),
                skill_ids=("review",),
            ),
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset(),
            ),
            "verification": DepartmentSpec(
                department_id="verification",
                mission="verification",
                agent_ids=("reviewer",),
                model_preferences=(),
                allowed_capabilities=frozenset(),
            ),
        },
    )


def test_team_coordinator_selects_distinct_adversarial_reviewers():
    org = organization()
    coordinator = TeamCoordinator(
        task_engine=TaskEngine(None, Path(".").resolve(), organization=org),
        agent_selector=AgentSelector(org),
        max_members=3,
    )

    panel = coordinator.select_adversarial_reviewers(
        objective="research Aurora evidence",
        current_roles=("researcher",),
        min_reviewers=2,
        max_reviewers=2,
    )

    assert len(panel) == 2
    assert {member.role for member in panel} == {"researcher", "reviewer"}


def test_team_coordinator_fails_closed_when_only_one_specialist_exists():
    org = organization()
    org.agents.pop("reviewer")
    org.departments.pop("verification")
    coordinator = TeamCoordinator(
        task_engine=TaskEngine(None, Path(".").resolve(), organization=org),
        agent_selector=AgentSelector(org),
    )

    try:
        coordinator.select_adversarial_reviewers(
            objective="research Aurora evidence",
            current_roles=("researcher",),
        )
    except TeamCoordinatorError as exc:
        assert "at least two distinct" in str(exc)
    else:
        raise AssertionError("single-specialist panel did not fail closed")


class FakePlanner:
    def plan(self, **kwargs):
        return DirectorPlan(
            objective=kwargs["objective"],
            tasks=(
                DelegatedTask(
                    "collect",
                    "researcher",
                    "collect evidence",
                    skills=("research",),
                ),
            ),
            model="fake/director",
        )

    def review(self, **kwargs):
        return DirectorReview(
            decision="complete",
            rationale="Evidence is sufficient after independent review.",
            tasks=(),
            model="fake/director",
        )


class FakeOrchestration:
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["task_id"].startswith("adversarial:"):
            verdict = "challenge" if kwargs["role"] == "reviewer" else "accept"
            content = (
                '{"verdict":"'
                + verdict
                + '","findings":["Independent verification required for the central claim."],'
                '"confidence":0.91}'
            )
        else:
            content = "completed " + kwargs["task_id"]
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model=f"model/{kwargs['role']}",
                provider="fake",
                content=content,
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
                requested_model=f"model/{kwargs['role']}",
                selected_model=f"model/{kwargs['role']}",
                provider_model=f"model/{kwargs['role']}",
                reported_model=f"model/{kwargs['role']}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("capability execution is not expected")


def test_director_runtime_injects_adversarial_evidence_into_review(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    orchestration = FakeOrchestration()
    org = organization()
    engine = TaskEngine(orchestration, tmp_path / "workspace", organization=org)
    director = DurableDirectorService(
        planner=FakePlanner(),
        task_engine=engine,
        store=store,
        event_bus=bus,
        team_coordinator=TeamCoordinator(
            task_engine=engine,
            agent_selector=AgentSelector(org),
            max_members=3,
        ),
    )
    runtime = DirectorRuntime(
        director=director,
        store=store,
        event_bus=bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=2,
            idle_sleep_seconds=0,
        ),
        worker_id="director-a",
        event_consumer_id="director-events",
        adversarial_review_enabled=True,
        adversarial_review_size=2,
    )

    submission = runtime.submit_mission(objective="research Aurora evidence")
    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED

    review_events = [
        event
        for event in store.events(submission.mission_id)
        if event.event_type == "director_adversarial_review"
    ]
    assert len(review_events) == 1
    review = review_events[0].payload
    assert review["status"] == "challenged"
    assert {item["role"] for item in review["reviewers"]} == {"researcher", "reviewer"}
    assert any("Independent verification" in item for item in review["findings"])

    assert all(
        call["use_workbench"] is False
        for call in orchestration.calls
        if call["task_id"].startswith("adversarial:")
    )
