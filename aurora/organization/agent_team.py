"""Dynamic specialist-team formation for Aurora missions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .agent_selection import AgentSelectionError, AgentSelector
from .registry import OrganizationRegistry


@dataclass(frozen=True)
class TeamMember:
    role: str
    score: float
    covered_skills: tuple[str, ...]
    department: str


@dataclass(frozen=True)
class AgentTeam:
    """Auditable team formed for one mission objective."""

    objective: str
    members: tuple[TeamMember, ...]
    required_skills: tuple[str, ...]
    covered_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    max_members: int

    @property
    def complete(self) -> bool:
        return not self.missing_skills


class AgentTeamError(RuntimeError):
    """Raised when a safe specialist team cannot be formed."""


class DynamicAgentTeamSelector:
    """Build the smallest authorized team that covers required specialist skills."""

    def __init__(self, organization: OrganizationRegistry, agent_selector: AgentSelector) -> None:
        self.organization = organization
        self.agent_selector = agent_selector

    def form(
        self,
        *,
        objective: str,
        required_skill_ids: Sequence[str] = (),
        capability: str | None = None,
        tags: Sequence[str] = (),
        preferred_roles: Sequence[str] = (),
        max_members: int = 4,
    ) -> AgentTeam:
        objective = objective.strip()
        if not objective:
            raise AgentTeamError("objective is empty")
        if max_members < 1:
            raise AgentTeamError("max_members must be at least 1")

        required = tuple(dict.fromkeys(skill.strip() for skill in required_skill_ids if skill.strip()))
        candidates: list[tuple[str, float, tuple[str, ...]]] = []

        for skill_id in required or ("__objective__",):
            try:
                decision = self.agent_selector.route(
                    objective=objective,
                    required_skill_ids=() if skill_id == "__objective__" else (skill_id,),
                    capability=capability,
                    tags=tags,
                    preferred_roles=preferred_roles,
                )
            except AgentSelectionError as exc:
                if skill_id == "__objective__":
                    raise AgentTeamError(str(exc)) from exc
                continue
            for role, score in decision.candidates:
                candidates.append((role, score, self._agent_skills(role, required)))

        by_role: dict[str, tuple[float, tuple[str, ...]]] = {}
        for role, score, covered in candidates:
            current = by_role.get(role)
            if current is None or score > current[0]:
                by_role[role] = (score, covered)

        ranked = sorted(
            ((role, score, covered) for role, (score, covered) in by_role.items()),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )

        selected: list[TeamMember] = []
        covered_skills: set[str] = set()
        remaining = set(required)

        while remaining and len(selected) < max_members:
            options = [item for item in ranked if item[0] not in {m.role for m in selected}]
            if not options:
                break
            best = max(
                options,
                key=lambda item: (len(set(item[2]) & remaining), item[1], item[0]),
            )
            new_coverage = set(best[2]) & remaining
            if not new_coverage:
                break
            role, score, _ = best
            agent = self.organization.resolve_agent(role)
            selected.append(
                TeamMember(
                    role=role,
                    score=score,
                    covered_skills=tuple(sorted(new_coverage)),
                    department=agent.department,
                )
            )
            covered_skills.update(new_coverage)
            remaining -= new_coverage

        if not required and not selected:
            role, score, _ = ranked[0]
            agent = self.organization.resolve_agent(role)
            selected.append(TeamMember(role=role, score=score, covered_skills=(), department=agent.department))

        return AgentTeam(
            objective=objective,
            members=tuple(selected),
            required_skills=required,
            covered_skills=tuple(sorted(covered_skills)),
            missing_skills=tuple(sorted(remaining)),
            max_members=max_members,
        )

    def _agent_skills(self, role: str, required: tuple[str, ...]) -> tuple[str, ...]:
        agent = self.organization.resolve_agent(role)
        if self.agent_selector.skill_registry is not None:
            assignment = self.agent_selector.skill_registry.for_agent(
                role=agent.role,
                agent_capabilities=agent.capabilities,
                default_skill_ids=agent.skill_ids,
                requested_skill_ids=(),
            )
            available = set(assignment.skill_ids)
        else:
            available = set(agent.skill_ids)
        return tuple(skill for skill in required if skill in available)


__all__ = ["AgentTeam", "AgentTeamError", "DynamicAgentTeamSelector", "TeamMember"]
