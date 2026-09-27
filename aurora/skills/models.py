"""Typed contracts for Aurora agent skills."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    version: str
    name: str
    mission: str
    instructions: str
    required_capabilities: frozenset[str]
    verification_requirements: tuple[str, ...]
    quality_checks: tuple[str, ...]
    tags: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    enabled: bool = True
    model_preferences: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillAssignment:
    """Resolved specialization for one agent task."""

    role: str
    skill_ids: tuple[str, ...]
    skills: tuple[SkillSpec, ...]

    @property
    def model_preferences(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for skill in self.skills:
            for model_id in skill.model_preferences:
                if model_id not in ordered:
                    ordered.append(model_id)
        return tuple(ordered)

    def render_system_context(self) -> str:
        if not self.skills:
            return ""
        sections = [
            "Aurora specialist skill profile.",
            "Treat these as governed operating procedures, not as permission "
            "to bypass Aurora capabilities or security policy.",
        ]
        for skill in self.skills:
            sections.append(
                f"Skill {skill.skill_id} v{skill.version}: {skill.name}\n"
                f"Mission: {skill.mission}\n"
                f"Procedure:\n{skill.instructions}\n"
                f"Verification required: {', '.join(skill.verification_requirements) or 'none'}\n"
                f"Quality checks: {', '.join(skill.quality_checks) or 'none'}"
            )
        return "\n\n".join(sections)
