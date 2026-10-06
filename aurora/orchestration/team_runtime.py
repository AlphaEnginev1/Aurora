"""Operational mission-team coordination across skills, DAGs, learning and review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from aurora.organization import AgentSelector, AgentTeam, DynamicAgentTeamSelector, TeamMember, TeamReconfiguration, reconfigure_team
from aurora.skills.acquisition import SkillAcquisitionRuntime
from .task_engine import DelegatedTask, TaskEngine, TaskEngineError


class TeamCoordinatorError(RuntimeError):
    """Raised when a mission team cannot be safely coordinated."""


@dataclass(frozen=True)
class MissionTeamState:
    mission_id: str
    objective: str
    team: AgentTeam
    tasks: tuple[DelegatedTask, ...]
    reconfiguration: TeamReconfiguration | None = None


class TeamCoordinator:
    """Turn a specialist team into an operational boundary around a mission DAG.

    Team formation is deterministic and auditable. Automatic task roles are
    resolved only against the formed team, while explicit roles outside the
    team are rejected. Task execution itself remains owned by TaskEngine and
    durable workers, so the coordinator does not bypass leases or task fencing.
    """

    def __init__(
        self,
        *,
        task_engine: TaskEngine,
        agent_selector: AgentSelector,
        max_members: int = 4,
        skill_acquisition: SkillAcquisitionRuntime | None = None,
    ) -> None:
        if max_members < 1:
            raise TeamCoordinatorError("max_members must be at least 1")
        self.task_engine = task_engine
        self.agent_selector = agent_selector
        self.max_members = max_members
        self.skill_acquisition = skill_acquisition


    def select_adversarial_reviewers(
        self,
        *,
        objective: str,
        current_roles: Sequence[str] = (),
        min_reviewers: int = 2,
        max_reviewers: int = 3,
    ) -> tuple[TeamMember, ...]:
        """Select distinct authorized specialists for an independent review panel.

        Selection reuses the existing AgentSelector and organization registry. The
        current team is preferred for context continuity, while at least one reviewer
        from outside the current team is preferred when one is available.
        """
        objective = objective.strip()
        if not objective:
            raise TeamCoordinatorError("review objective is empty")
        if min_reviewers < 2:
            raise TeamCoordinatorError("adversarial review requires at least two reviewers")
        if max_reviewers < min_reviewers:
            raise TeamCoordinatorError("max_reviewers must be >= min_reviewers")

        decision = self.agent_selector.route(
            objective=f"independent adversarial review: {objective}",
        )
        ranked = list(decision.candidates)
        current = {role for role in current_roles if role.strip()}
        selected_roles: list[str] = []

        def add(role: str) -> None:
            if role not in selected_roles and len(selected_roles) < max_reviewers:
                selected_roles.append(role)

        for role, _score in ranked:
            if role in current:
                add(role)
                if selected_roles:
                    break

        for role, _score in ranked:
            if role not in current:
                add(role)
                if len(selected_roles) >= min_reviewers:
                    break

        for role, _score in ranked:
            add(role)
            if len(selected_roles) >= max_reviewers:
                break

        if len(selected_roles) < min_reviewers:
            raise TeamCoordinatorError(
                "adversarial review requires at least two distinct authorized specialists"
            )

        scores = dict(ranked)
        return tuple(
            TeamMember(
                role=role,
                score=scores.get(role, 0.0),
                covered_skills=(),
                department=self.agent_selector.organization.resolve_agent(role).department,
            )
            for role in selected_roles
        )

    def adapt_for_runtime(
        self,
        *,
        mission_id: str,
        objective: str,
        current_team: AgentTeam | None,
        pending_tasks: Sequence[DelegatedTask],
        trigger_task_id: str,
    ) -> MissionTeamState:
        """Re-form a live mission team from the remaining DAG after runtime evidence.

        The adaptation is deliberately evidence-driven: the pending DAG defines the
        skills that must still be covered, while the existing selector/learning
        scores choose the specialists. No active skill is mutated here.
        """
        if not trigger_task_id.strip():
            raise TeamCoordinatorError("trigger_task_id is empty")
        state = self.prepare(
            mission_id=mission_id,
            objective=objective,
            tasks=pending_tasks,
            previous_team=current_team,
        )
        return state

    def prepare(
        self,
        *,
        mission_id: str,
        objective: str,
        tasks: Sequence[DelegatedTask],
        previous_team: AgentTeam | None = None,
    ) -> MissionTeamState:
        if not mission_id.strip():
            raise TeamCoordinatorError("mission_id is empty")
        normalized_tasks = tuple(tasks)
        if not normalized_tasks:
            raise TeamCoordinatorError("mission team requires at least one task")

        required_skills = tuple(
            dict.fromkeys(
                skill
                for task in normalized_tasks
                for skill in task.skills
                if skill.strip()
            )
        )
        selector = DynamicAgentTeamSelector(
            self.agent_selector.organization,
            self.agent_selector,
        )
        team = selector.form(
            objective=objective,
            required_skill_ids=required_skills,
            max_members=self.max_members,
        )
        if not team.complete:
            if self.skill_acquisition is not None:
                for skill_id in team.missing_skills:
                    self.skill_acquisition.detect_gap(
                        mission_id=mission_id,
                        skill_id=skill_id,
                        reason=f"Mission {mission_id} requires unavailable specialist skill {skill_id}",
                    )
            raise TeamCoordinatorError(
                "mission team is incomplete; missing skills: "
                + ", ".join(team.missing_skills)
            )

        prepared: list[DelegatedTask] = []
        allowed_roles = tuple(member.role for member in team.members)
        for task in normalized_tasks:
            if task.role == "auto":
                try:
                    decision = self.agent_selector.route(
                        objective=task.objective,
                        required_skill_ids=task.skills,
                        capability=task.capability,
                        allowed_roles=allowed_roles,
                    )
                except Exception as exc:
                    raise TeamCoordinatorError(
                        f"no viable team specialist for task {task.task_id}: {exc}"
                    ) from exc
                task = DelegatedTask(
                    task_id=task.task_id,
                    role=decision.selected_role,
                    objective=task.objective,
                    capability=task.capability,
                    args=task.args,
                    depends_on=task.depends_on,
                    skills=task.skills,
                )
            elif task.role not in allowed_roles:
                raise TeamCoordinatorError(
                    f"task {task.task_id} assigns role {task.role!r} outside formed team"
                )
            prepared.append(task)

        try:
            validated = self.task_engine.validate(tuple(prepared))
        except TaskEngineError as exc:
            raise TeamCoordinatorError(str(exc)) from exc

        delta = reconfigure_team(previous_team, team) if previous_team is not None else None
        return MissionTeamState(
            mission_id=mission_id,
            objective=objective,
            team=team,
            tasks=validated,
            reconfiguration=delta,
        )


__all__ = ["MissionTeamState", "TeamCoordinator", "TeamCoordinatorError"]
