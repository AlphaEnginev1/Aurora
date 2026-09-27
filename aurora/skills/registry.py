"""Versioned registry and policy enforcement for Aurora skills."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .models import SkillAssignment, SkillSpec

if TYPE_CHECKING:
    from aurora.organization import OrganizationRegistry


class SkillRegistryError(ValueError):
    """Raised when a skill is unknown, invalid, or not safely assignable."""


class SkillRegistry:
    """Load trusted skill definitions and resolve governed specializations."""

    def __init__(self, skills: dict[str, SkillSpec]) -> None:
        self.skills = dict(skills)
        self._validate()

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        configured_models: set[str] | frozenset[str] | None = None,
    ) -> "SkillRegistry":
        source = Path(path)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillRegistryError(f"Unable to load skill registry: {source}") from exc

        if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
            raise SkillRegistryError("'skills' must be a non-empty list")
        if not data["skills"]:
            raise SkillRegistryError("'skills' must be a non-empty list")

        skills: dict[str, SkillSpec] = {}
        for item in data["skills"]:
            if not isinstance(item, dict):
                raise SkillRegistryError("each skill must be an object")
            required = ("skill_id", "version", "name", "mission", "instructions")
            if not all(
                isinstance(item.get(key), str) and item[key].strip()
                for key in required
            ):
                raise SkillRegistryError(
                    "skill requires non-empty skill_id, version, name, mission and instructions"
                )

            required_capabilities = item.get("required_capabilities", [])
            verification_requirements = item.get("verification_requirements", [])
            quality_checks = item.get("quality_checks", [])
            tags = item.get("tags", [])
            dependencies = item.get("dependencies", [])
            model_preferences = item.get("model_preferences", [])
            enabled = item.get("enabled", True)

            for field_name, values in (
                ("required_capabilities", required_capabilities),
                ("verification_requirements", verification_requirements),
                ("quality_checks", quality_checks),
                ("tags", tags),
                ("dependencies", dependencies),
                ("model_preferences", model_preferences),
            ):
                if not isinstance(values, list) or not all(
                    isinstance(value, str) and value.strip() for value in values
                ):
                    raise SkillRegistryError(
                        f"invalid {field_name} for skill {item.get('skill_id')!r}"
                    )
            if not isinstance(enabled, bool):
                raise SkillRegistryError(
                    f"invalid enabled flag for skill {item.get('skill_id')!r}"
                )

            skill = SkillSpec(
                skill_id=item["skill_id"],
                version=item["version"],
                name=item["name"],
                mission=item["mission"],
                instructions=item["instructions"],
                required_capabilities=frozenset(required_capabilities),
                verification_requirements=tuple(verification_requirements),
                quality_checks=tuple(quality_checks),
                tags=tuple(tags),
                dependencies=tuple(dependencies),
                enabled=enabled,
                model_preferences=tuple(model_preferences),
            )
            if skill.skill_id in skills:
                raise SkillRegistryError(f"duplicate skill_id: {skill.skill_id}")
            skills[skill.skill_id] = skill

        registry = cls(skills)
        if configured_models is not None:
            registry.validate_model_preferences(set(configured_models))
        return registry

    def _validate(self) -> None:
        for skill in self.skills.values():
            if not skill.enabled:
                continue
            if skill.skill_id in skill.dependencies:
                raise SkillRegistryError(
                    f"skill cannot depend on itself: {skill.skill_id}"
                )
            unknown = sorted(dep for dep in skill.dependencies if dep not in self.skills)
            if unknown:
                raise SkillRegistryError(
                    f"skill {skill.skill_id} references unknown dependency: {', '.join(unknown)}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(skill_id: str) -> None:
            if skill_id in visiting:
                raise SkillRegistryError("skill dependency graph contains a cycle")
            if skill_id in visited:
                return
            visiting.add(skill_id)
            for dependency in self.skills[skill_id].dependencies:
                visit(dependency)
            visiting.remove(skill_id)
            visited.add(skill_id)

        for skill_id in self.skills:
            visit(skill_id)

    def validate_model_preferences(self, configured_models: set[str]) -> None:
        for skill in self.skills.values():
            for model_id in skill.model_preferences:
                if model_id not in configured_models:
                    raise SkillRegistryError(
                        "skill references model not present in inference policy: "
                        f"{model_id}"
                    )

    def resolve(self, skill_id: str) -> SkillSpec:
        key = skill_id.strip()
        if not key:
            raise SkillRegistryError("skill_id is empty")
        skill = self.skills.get(key)
        if skill is None:
            raise SkillRegistryError(f"unknown Aurora skill: {skill_id}")
        if not skill.enabled:
            raise SkillRegistryError(f"skill is disabled: {skill_id}")
        return skill

    def for_agent(
        self,
        *,
        role: str,
        agent_capabilities: frozenset[str],
        default_skill_ids: tuple[str, ...] = (),
        requested_skill_ids: tuple[str, ...] = (),
    ) -> SkillAssignment:
        ordered_ids: list[str] = []
        for skill_id in (*default_skill_ids, *requested_skill_ids):
            if skill_id not in ordered_ids:
                ordered_ids.append(skill_id)

        resolved: list[SkillSpec] = []
        for skill_id in ordered_ids:
            skill = self.resolve(skill_id)
            missing = sorted(skill.required_capabilities - agent_capabilities)
            if missing:
                raise SkillRegistryError(
                    f"skill {skill_id} requires unauthorized capabilities for {role}: "
                    + ", ".join(missing)
                )
            for dependency in skill.dependencies:
                if dependency not in ordered_ids:
                    raise SkillRegistryError(
                        f"skill {skill_id} requires skill dependency {dependency}"
                    )
            resolved.append(skill)

        return SkillAssignment(
            role=role,
            skill_ids=tuple(ordered_ids),
            skills=tuple(resolved),
        )

    def validate_organization(self, organization: "OrganizationRegistry") -> None:
        for agent in organization.agents.values():
            self.for_agent(
                role=agent.role,
                agent_capabilities=agent.capabilities,
                default_skill_ids=agent.skill_ids,
            )

    def catalog(self) -> str:
        """Return a compact trusted skill catalog for Director planning."""
        lines = []
        for skill_id in sorted(self.skills):
            skill = self.skills[skill_id]
            if not skill.enabled:
                continue
            required = ", ".join(sorted(skill.required_capabilities)) or "none"
            lines.append(
                f"- {skill.skill_id} v{skill.version}: {skill.name}; "
                f"mission={skill.mission}; required_capabilities={required}"
            )
        return "\n".join(lines)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.skills))

    def describe_for_role(
        self,
        *,
        role: str,
        agent_capabilities: frozenset[str],
        default_skill_ids: tuple[str, ...],
    ) -> str:
        assignment = self.for_agent(
            role=role,
            agent_capabilities=agent_capabilities,
            default_skill_ids=default_skill_ids,
        )
        if not assignment.skills:
            return "No default specialist skills assigned."
        return "\n".join(
            f"- {skill.skill_id} v{skill.version}: {skill.name}"
            for skill in assignment.skills
        )
