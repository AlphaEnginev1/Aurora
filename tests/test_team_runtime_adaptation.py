from pathlib import Path

from aurora.organization import AgentSelector, AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine
from aurora.orchestration.team_runtime import TeamCoordinator


def org():
    return OrganizationRegistry(
        agents={
            "researcher": AgentSpec("researcher", "researcher", "research", "research evidence", frozenset({"web"}), (), ("research",)),
            "engineer": AgentSpec("engineer", "engineer", "engineering", "implement software", frozenset({"code"}), (), ("engineering",)),
        },
        departments={
            "research": DepartmentSpec("research", "research", ("researcher",), (), frozenset({"web"})),
            "engineering": DepartmentSpec("engineering", "engineering", ("engineer",), (), frozenset({"code"})),
        },
    )


def test_runtime_adaptation_reforms_team_from_pending_dag():
    organization = org()
    engine = TaskEngine(None, Path(".").resolve(), organization=organization)
    coordinator = TeamCoordinator(task_engine=engine, agent_selector=AgentSelector(organization), max_members=2)
    current = coordinator.prepare(
        mission_id="m1",
        objective="research and implement",
        tasks=(DelegatedTask("research", "auto", "research evidence", skills=("research",)),),
    )
    adapted = coordinator.adapt_for_runtime(
        mission_id="m1",
        objective="research and implement",
        current_team=current.team,
        pending_tasks=(
            DelegatedTask("build", "auto", "implement software", skills=("engineering",)),
            DelegatedTask("verify", "auto", "verify implementation", depends_on=("build",)),
        ),
        trigger_task_id="build",
    )
    assert adapted.team.complete
    assert {member.role for member in adapted.team.members} == {"engineer"}
    assert adapted.tasks[0].role == "engineer"


def test_runtime_adaptation_fails_closed_for_uncovered_skill():
    organization = org()
    engine = TaskEngine(None, Path(".").resolve(), organization=organization)
    coordinator = TeamCoordinator(task_engine=engine, agent_selector=AgentSelector(organization), max_members=2)
    try:
        coordinator.adapt_for_runtime(
            mission_id="m2",
            objective="discover",
            current_team=None,
            pending_tasks=(DelegatedTask("x", "auto", "discover novel things", skills=("discovery",)),),
            trigger_task_id="x",
        )
    except Exception as exc:
        assert "missing skills" in str(exc) or "viable" in str(exc)
    else:
        raise AssertionError("uncovered runtime skill did not fail closed")
