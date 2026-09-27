from pathlib import Path

from aurora.organization import AgentSelector, AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine
from aurora.orchestration.team_runtime import TeamCoordinator, TeamCoordinatorError


def registry():
    return OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research evidence",
                capabilities=frozenset({"web"}),
                model_preferences=(),
                skill_ids=("research",),
            ),
            "engineer": AgentSpec(
                agent_id="engineer",
                role="engineer",
                department="engineering",
                mission="implement software",
                capabilities=frozenset({"code"}),
                model_preferences=(),
                skill_ids=("engineering",),
            ),
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset({"web"}),
            ),
            "engineering": DepartmentSpec(
                department_id="engineering",
                mission="engineering",
                agent_ids=("engineer",),
                model_preferences=(),
                allowed_capabilities=frozenset({"code"}),
            ),
        },
    )


def test_team_coordinator_resolves_auto_tasks_inside_formed_team():
    org = registry()
    engine = TaskEngine(None, Path(".").resolve(), organization=org)
    coordinator = TeamCoordinator(
        task_engine=engine,
        agent_selector=AgentSelector(org),
        max_members=2,
    )
    state = coordinator.prepare(
        mission_id="mission-1",
        objective="research and implement",
        tasks=(
            DelegatedTask("research", "auto", "research evidence", skills=("research",)),
            DelegatedTask("build", "auto", "implement software", skills=("engineering",)),
            DelegatedTask("integrate", "auto", "integrate results", depends_on=("research", "build")),
        ),
    )

    assert state.team.complete
    assert {member.role for member in state.team.members} == {"researcher", "engineer"}
    assert state.tasks[0].role == "researcher"
    assert state.tasks[1].role == "engineer"
    assert state.tasks[2].role in {"researcher", "engineer"}


def test_team_coordinator_rejects_role_outside_team():
    org = registry()
    engine = TaskEngine(None, Path(".").resolve(), organization=org)
    coordinator = TeamCoordinator(
        task_engine=engine,
        agent_selector=AgentSelector(org),
    )
    try:
        coordinator.prepare(
            mission_id="mission-2",
            objective="research only",
            tasks=(DelegatedTask("x", "engineer", "implement"),),
        )
    except TeamCoordinatorError as exc:
        assert "outside formed team" in str(exc)
    else:
        raise AssertionError("team boundary was not enforced")
