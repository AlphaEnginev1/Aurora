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


def test_team_coordinator_preserves_explicit_role_in_plan():
    org = registry()
    engine = TaskEngine(None, Path(".").resolve(), organization=org)
    coordinator = TeamCoordinator(
        task_engine=engine,
        agent_selector=AgentSelector(org),
    )
    state = coordinator.prepare(
        mission_id="mission-2",
        objective="research only",
        tasks=(DelegatedTask("x", "engineer", "implement"),),
    )

    assert state.team.complete
    assert {member.role for member in state.team.members} == {"engineer"}
    assert state.tasks[0].role == "engineer"


def test_team_coordinator_treats_explicit_task_skill_as_assigned_coverage():
    from aurora.skills.models import SkillSpec
    from aurora.skills.registry import SkillRegistry

    org = OrganizationRegistry(
        agents={
            "engineer": AgentSpec(
                agent_id="engineer",
                role="engineer",
                department="engineering",
                mission="implement software",
                capabilities=frozenset({"workbench:*"}),
                model_preferences=(),
                skill_ids=(),
                universal_access=True,
            ),
        },
        departments={
            "engineering": DepartmentSpec(
                department_id="engineering",
                mission="engineering",
                agent_ids=("engineer",),
                model_preferences=(),
                allowed_capabilities=frozenset({"workbench:*"}),
            ),
        },
    )
    skill_registry = SkillRegistry({
        "discovery-hypothesis-generation": SkillSpec(
            skill_id="discovery-hypothesis-generation",
            version="1.0.0",
            name="Hypothesis generation",
            mission="Explore alternatives",
            instructions="Use evidence and do not treat hypotheses as facts.",
            required_capabilities=frozenset({"tool:rg"}),
            verification_requirements=(),
            quality_checks=(),
            tags=("discovery",),
        )
    })
    engine = TaskEngine(
        None,
        Path(".").resolve(),
        organization=org,
        skill_registry=skill_registry,
    )
    coordinator = TeamCoordinator(
        task_engine=engine,
        agent_selector=AgentSelector(org, skill_registry),
    )

    state = coordinator.prepare(
        mission_id="mission-explicit-skill",
        objective="engineer investigates runtime",
        tasks=(
            DelegatedTask(
                "inspect",
                "engineer",
                "inspect governed runtime design",
                skills=("discovery-hypothesis-generation",),
            ),
        ),
    )

    assert state.team.complete
    assert {member.role for member in state.team.members} == {"engineer"}
    assert state.team.required_skills == ("discovery-hypothesis-generation",)
    assert state.team.covered_skills == ("discovery-hypothesis-generation",)
    assert state.team.missing_skills == ()
    assert state.tasks[0].skills == ("discovery-hypothesis-generation",)
