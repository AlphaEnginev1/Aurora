"""Evidence-backed adaptive selection of specialist agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .registry import AgentSpec, OrganizationRegistry


@dataclass(frozen=True)
class AgentSelection:
    """Auditable agent selection decision."""

    selected_role: str
    selected_score: float
    candidates: tuple[tuple[str, float], ...]
    factors: dict[str, float]


class AgentSelectionError(RuntimeError):
    """Raised when no authorized specialist can execute a task."""


class AgentSelector:
    """Select an authorized specialist using skills, capability and evidence."""

    def __init__(
        self,
        organization: OrganizationRegistry,
        skill_registry: object | None = None,
        skill_learning: object | None = None,
    ) -> None:
        self.organization = organization
        self.skill_registry = skill_registry
        self.skill_learning = skill_learning

    def select(
        self,
        *,
        objective: str,
        required_skill_ids: Sequence[str] = (),
        capability: str | None = None,
        tags: Sequence[str] = (),
        preferred_roles: Sequence[str] = (),
        allowed_roles: Sequence[str] = (),
    ) -> str:
        return self.route(
            objective=objective,
            required_skill_ids=required_skill_ids,
            capability=capability,
            tags=tags,
            preferred_roles=preferred_roles,
            allowed_roles=allowed_roles,
        ).selected_role

    def route(
        self,
        *,
        objective: str,
        required_skill_ids: Sequence[str] = (),
        capability: str | None = None,
        tags: Sequence[str] = (),
        preferred_roles: Sequence[str] = (),
        allowed_roles: Sequence[str] = (),
    ) -> AgentSelection:
        from aurora.skills import SkillRegistryError

        required = tuple(dict.fromkeys(required_skill_ids))
        tagset = {tag.lower() for tag in tags}
        preferred = set(preferred_roles)
        allowed = set(allowed_roles)
        learned = self._learned_scores(required)

        viable: list[tuple[AgentSpec, float, dict[str, float]]] = []
        for agent in self.organization.agents.values():
            if allowed and agent.role not in allowed:
                continue
            if capability is not None and capability not in agent.capabilities:
                continue
            assignment_skill_ids = tuple(agent.skill_ids)
            assignment_tags: tuple[str, ...] = ()
            if self.skill_registry is not None:
                try:
                    assignment = self.skill_registry.for_agent(
                        role=agent.role,
                        agent_capabilities=agent.capabilities,
                        default_skill_ids=agent.skill_ids,
                        requested_skill_ids=required,
                    )
                except SkillRegistryError:
                    continue
                assignment_skill_ids = assignment.skill_ids
                assignment_tags = assignment.tags
                available_skills = set(assignment_skill_ids)
                if any(skill_id not in available_skills for skill_id in required):
                    continue
            elif required and any(skill_id not in assignment_skill_ids for skill_id in required):
                continue

            skill_match = (
                len(set(assignment_skill_ids) & set(required)) / len(required)
                if required
                else 0.5
            )
            objective_match = self._text_match(objective, agent.mission)
            tag_match = self._tag_match(tagset, assignment_tags or assignment_skill_ids)
            learned_score = learned.get(agent.role, 0.5)
            preference = 1.0 if agent.role in preferred else 0.0
            capability_fit = 1.0 if capability is None or capability in agent.capabilities else 0.0

            factors = {
                "skill_match": skill_match,
                "objective_match": objective_match,
                "tag_match": tag_match,
                "learned_score": learned_score,
                "preference": preference,
                "capability_fit": capability_fit,
            }
            score = (
                0.35 * skill_match
                + 0.20 * objective_match
                + 0.10 * tag_match
                + 0.25 * learned_score
                + 0.05 * preference
                + 0.05 * capability_fit
            )
            viable.append((agent, score, factors))

        if not viable:
            raise AgentSelectionError("no viable authorized specialist agent")

        ranked = sorted(viable, key=lambda item: (item[1], item[0].role), reverse=True)
        selected, score, factors = ranked[0]
        return AgentSelection(
            selected_role=selected.role,
            selected_score=score,
            candidates=tuple((agent.role, value) for agent, value, _ in ranked),
            factors=factors,
        )

    def _learned_scores(self, skill_ids: tuple[str, ...]) -> dict[str, float]:
        if self.skill_learning is None or not skill_ids:
            return {}
        method = getattr(self.skill_learning, "agent_scores_for", None)
        if method is None:
            return {}
        values = method(skill_ids)
        return {
            str(role): max(0.0, min(1.0, float(score)))
            for role, score in values.items()
        }

    @staticmethod
    def _text_match(objective: str, mission: str) -> float:
        objective_terms = {
            token.strip(".,:;()[]{}").lower()
            for token in objective.split()
            if len(token.strip(".,:;()[]{}")) >= 4
        }
        mission_terms = {
            token.strip(".,:;()[]{}").lower()
            for token in mission.split()
            if len(token.strip(".,:;()[]{}")) >= 4
        }
        if not objective_terms or not mission_terms:
            return 0.5
        overlap = len(objective_terms & mission_terms)
        return min(1.0, overlap / max(1, len(objective_terms)))

    @staticmethod
    def _tag_match(tags: set[str], skill_ids: Sequence[str]) -> float:
        if not tags:
            return 0.5
        normalized = {skill.lower() for skill in skill_ids}
        return min(1.0, len(tags & normalized) / len(tags))


__all__ = ["AgentSelection", "AgentSelectionError", "AgentSelector"]
