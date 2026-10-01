"""Declarative project registry for Aurora.

The project registry is the authoritative catalog of Aurora-managed projects.
It stores canonical project metadata and validates references to registered
agents, skills and models when those catalogs are supplied by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Collection


class ProjectConfigError(ValueError):
    """Raised when the Aurora project registry is invalid."""


class ProjectStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class ProjectSpec:
    """Canonical metadata for one Aurora-managed project."""

    project_id: str
    name: str
    objective: str
    status: ProjectStatus
    repository: str | None = None
    workspace: str | None = None
    architecture_ref: str | None = None
    agent_roles: tuple[str, ...] = ()
    skill_ids: tuple[str, ...] = ()
    model_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    current_state: str = ""
    created_at: str = ""
    updated_at: str = ""


class ProjectRegistry:
    """Load, validate and resolve Aurora project definitions."""

    def __init__(self, projects: dict[str, ProjectSpec]) -> None:
        self.projects = dict(projects)
        self._validate()

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        configured_agents: Collection[str] | None = None,
        configured_skills: Collection[str] | None = None,
        configured_models: Collection[str] | None = None,
    ) -> "ProjectRegistry":
        source = Path(path)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectConfigError(
                f"Unable to load project registry: {source}"
            ) from exc

        if not isinstance(data, dict):
            raise ProjectConfigError("project registry must be an object")
        raw_projects = data.get("projects")
        if not isinstance(raw_projects, list):
            raise ProjectConfigError("'projects' must be a list")

        projects: dict[str, ProjectSpec] = {}
        for item in raw_projects:
            if not isinstance(item, dict):
                raise ProjectConfigError("each project must be an object")

            required = ("project_id", "name", "objective", "status")
            if not all(
                isinstance(item.get(key), str) and item[key].strip()
                for key in required
            ):
                raise ProjectConfigError(
                    "project requires non-empty project_id, name, objective and status"
                )

            try:
                status = ProjectStatus(item["status"].strip().lower())
            except ValueError as exc:
                allowed = ", ".join(value.value for value in ProjectStatus)
                raise ProjectConfigError(
                    f"invalid project status {item.get('status')!r}; expected one of: {allowed}"
                ) from exc

            sequence_fields = (
                "agent_roles",
                "skill_ids",
                "model_ids",
                "task_ids",
                "decision_ids",
            )
            for field_name in sequence_fields:
                values = item.get(field_name, [])
                if not isinstance(values, list) or not all(
                    isinstance(value, str) and value.strip() for value in values
                ):
                    raise ProjectConfigError(
                        f"invalid {field_name} for project {item.get('project_id')!r}"
                    )

            scalar_optional = (
                "repository",
                "workspace",
                "architecture_ref",
                "current_state",
                "created_at",
                "updated_at",
            )
            for field_name in scalar_optional:
                value = item.get(field_name)
                if value is not None and not isinstance(value, str):
                    raise ProjectConfigError(
                        f"{field_name} must be a string or null for project {item.get('project_id')!r}"
                    )

            project = ProjectSpec(
                project_id=item["project_id"].strip(),
                name=item["name"].strip(),
                objective=item["objective"].strip(),
                status=status,
                repository=_clean_optional(item.get("repository")),
                workspace=_clean_optional(item.get("workspace")),
                architecture_ref=_clean_optional(item.get("architecture_ref")),
                agent_roles=_dedupe(item.get("agent_roles", [])),
                skill_ids=_dedupe(item.get("skill_ids", [])),
                model_ids=_dedupe(item.get("model_ids", [])),
                task_ids=_dedupe(item.get("task_ids", [])),
                decision_ids=_dedupe(item.get("decision_ids", [])),
                current_state=(item.get("current_state") or "").strip(),
                created_at=(item.get("created_at") or "").strip(),
                updated_at=(item.get("updated_at") or "").strip(),
            )
            if project.project_id in projects:
                raise ProjectConfigError(
                    f"duplicate project_id: {project.project_id}"
                )
            projects[project.project_id] = project

        registry = cls(projects)
        if configured_agents is not None:
            registry.validate_agent_references(set(configured_agents))
        if configured_skills is not None:
            registry.validate_skill_references(set(configured_skills))
        if configured_models is not None:
            registry.validate_model_references(set(configured_models))
        return registry

    def _validate(self) -> None:
        for project in self.projects.values():
            if not project.project_id.strip():
                raise ProjectConfigError("project_id is empty")
            if not project.name.strip():
                raise ProjectConfigError(
                    f"project {project.project_id} has an empty name"
                )
            if not project.objective.strip():
                raise ProjectConfigError(
                    f"project {project.project_id} has an empty objective"
                )
            if len(set(project.agent_roles)) != len(project.agent_roles):
                raise ProjectConfigError(
                    f"project {project.project_id} contains duplicate agent roles"
                )
            if len(set(project.skill_ids)) != len(project.skill_ids):
                raise ProjectConfigError(
                    f"project {project.project_id} contains duplicate skills"
                )
            if len(set(project.model_ids)) != len(project.model_ids):
                raise ProjectConfigError(
                    f"project {project.project_id} contains duplicate models"
                )
            if len(set(project.task_ids)) != len(project.task_ids):
                raise ProjectConfigError(
                    f"project {project.project_id} contains duplicate task ids"
                )
            if len(set(project.decision_ids)) != len(project.decision_ids):
                raise ProjectConfigError(
                    f"project {project.project_id} contains duplicate decision ids"
                )

    def validate_agent_references(self, configured_agents: set[str]) -> None:
        self._validate_references(
            "agent role", configured_agents,
            (item for project in self.projects.values() for item in project.agent_roles),
        )

    def validate_skill_references(self, configured_skills: set[str]) -> None:
        self._validate_references(
            "skill", configured_skills,
            (item for project in self.projects.values() for item in project.skill_ids),
        )

    def validate_model_references(self, configured_models: set[str]) -> None:
        self._validate_references(
            "model", configured_models,
            (item for project in self.projects.values() for item in project.model_ids),
        )

    @staticmethod
    def _validate_references(
        kind: str,
        configured: set[str],
        references,
    ) -> None:
        unknown = sorted({value for value in references if value not in configured})
        if unknown:
            raise ProjectConfigError(
                f"project references unknown {kind}: {', '.join(unknown)}"
            )

    def resolve(self, project_id: str) -> ProjectSpec:
        key = project_id.strip()
        if not key:
            raise ProjectConfigError("project_id is empty")
        project = self.projects.get(key)
        if project is None:
            raise ProjectConfigError(f"unknown Aurora project: {project_id}")
        return project

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.projects))

    def active(self) -> tuple[ProjectSpec, ...]:
        return tuple(
            project
            for project in self.projects.values()
            if project.status is ProjectStatus.ACTIVE
        )

    def catalog(self) -> str:
        """Return a compact trusted project catalog for Director context."""
        lines: list[str] = []
        for project in sorted(self.projects.values(), key=lambda item: item.project_id):
            state = project.current_state or "state not recorded"
            lines.append(
                f"- {project.project_id}: {project.name}; "
                f"status={project.status.value}; objective={project.objective}; state={state}"
            )
        return "\n".join(lines) if lines else "No Aurora projects are registered."

    def summary(self) -> dict[str, object]:
        """Return a UI-safe index without exposing full project contents."""
        return {
            "count": len(self.projects),
            "active_count": len(self.active()),
            "project_ids": list(self.ids()),
        }


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values))


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


__all__ = [
    "ProjectConfigError",
    "ProjectRegistry",
    "ProjectSpec",
    "ProjectStatus",
]
