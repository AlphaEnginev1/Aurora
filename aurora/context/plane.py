"""Authoritative Aurora context plane and fail-closed context gate.

This module establishes the common context contract used by the Director and
future specialist/project model executions. It does not replace durable task,
memory, research, or event stores; it composes their authoritative indexes
without treating model output as system truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Iterable, Mapping

from aurora.inference.models import ModelSpec
from aurora.organization import OrganizationRegistry
from aurora.projects import ProjectRegistry, ProjectSpec
from aurora.inference.registry import ModelRegistry
from aurora.skills import SkillRegistry


class AuroraContextError(RuntimeError):
    """Raised when authoritative Aurora context cannot be built safely."""


class ContextScope(StrEnum):
    DIRECTOR = "director"
    GLOBAL = "global"
    PROJECT = "project"
    AGENT = "agent"
    TASK = "task"


@dataclass(frozen=True)
class ContextFragment:
    """One authoritative or explicitly unknown context fragment."""

    source: str
    content: str
    scope: ContextScope
    authoritative: bool = True
    provenance: str = "aurora"
    captured_at: str = ""


@dataclass(frozen=True)
class ContextManifest:
    """Immutable manifest describing exactly what context was supplied."""

    manifest_id: str
    created_at: str
    scope: ContextScope
    project_id: str | None
    fragments: tuple[ContextFragment, ...]
    unknown_fields: tuple[str, ...] = ()

    @property
    def messages(self) -> list[dict[str, str]]:
        return [
            {
                "role": "system" if fragment.authoritative else "user",
                "content": (
                    f"[aurora-context source={fragment.source} "
                    f"scope={fragment.scope.value} "
                    f"provenance={fragment.provenance}]\n"
                    f"{fragment.content}"
                ),
            }
            for fragment in self.fragments
            if fragment.content.strip()
        ]


@dataclass
class AuroraContextPlane:
    """Compose authoritative Aurora indexes into a bounded context manifest."""

    project_registry: ProjectRegistry
    organization: OrganizationRegistry
    skill_registry: SkillRegistry
    models: tuple[ModelSpec, ...] = ()
    model_registry: ModelRegistry | None = None
    runtime_context: Callable[[str | None], Mapping[str, object]] | None = None
    mailbox_context: Callable[[str | None], Iterable[Mapping[str, object]]] | None = None
    knowledge_context: Callable[[str | None], Iterable[Mapping[str, object]]] | None = None

    def build(
        self,
        *,
        scope: ContextScope,
        project_id: str | None = None,
        agent_role: str | None = None,
        task_id: str | None = None,
    ) -> ContextManifest:
        normalized_project = _normalize_optional(project_id)
        normalized_agent = _normalize_optional(agent_role)
        _normalize_optional(task_id)

        selected_project: ProjectSpec | None = None
        if normalized_project is not None:
            try:
                selected_project = self.project_registry.resolve(normalized_project)
            except Exception as exc:
                raise AuroraContextError(str(exc)) from exc

        fragments: list[ContextFragment] = [
            self._fragment(
                source="aurora.identity",
                scope=scope,
                content="Aurora is the authoritative system of record for its managed projects, organization, skills, models, tasks, decisions, knowledge, events and governance state.",
            ),
            self._fragment(
                source="projects.catalog",
                scope=scope,
                content=self.project_registry.catalog(),
            ),
            self._fragment(
                source="organization.catalog",
                scope=scope,
                content=_organization_catalog(self.organization),
            ),
            self._fragment(
                source="skills.catalog",
                scope=scope,
                content=self.skill_registry.catalog(),
            ),
            self._fragment(
                source="models.catalog",
                scope=scope,
                content=_model_catalog(self.models, self.model_registry),
            ),
        ]
        unknown: set[str] = set()

        if selected_project is not None:
            fragments.append(
                self._fragment(
                    source=f"projects.{selected_project.project_id}",
                    scope=ContextScope.PROJECT,
                    content=_project_detail(selected_project),
                    provenance="project-registry",
                )
            )
        elif scope is ContextScope.PROJECT:
            unknown.add("project")
        elif scope in {ContextScope.AGENT, ContextScope.TASK} and normalized_project is None:
            unknown.add("project_scope")

        if normalized_agent is not None:
            try:
                agent = self.organization.resolve_agent(normalized_agent)
                fragments.append(
                    self._fragment(
                        source=f"organization.agent.{agent.role}",
                        scope=ContextScope.AGENT,
                        content=(
                            f"role={agent.role}\n"
                            f"department={agent.department}\n"
                            f"mission={agent.mission}\n"
                            f"capabilities={', '.join(sorted(agent.capabilities)) or 'none'}\n"
                            f"skills={', '.join(agent.skill_ids) or 'none'}\n"
                            f"model_preferences={', '.join(agent.model_preferences) or 'none'}"
                        ),
                        provenance="organization-registry",
                    )
                )
            except Exception as exc:
                raise AuroraContextError(str(exc)) from exc

        if self.runtime_context is not None:
            runtime = dict(self.runtime_context(normalized_project))
            fragments.append(
                self._fragment(
                    source="runtime.state",
                    scope=scope,
                    content=_render_mapping(runtime, unknown),
                    provenance="runtime-store",
                )
            )
        else:
            unknown.add("runtime_state")

        if self.mailbox_context is not None:
            mailbox = tuple(self.mailbox_context(normalized_project))
            fragments.extend(
                self._mapping_fragments(
                    source="mailbox",
                    rows=mailbox,
                    scope=scope,
                    provenance="mailbox-store",
                    unknown=unknown,
                )
            )
        else:
            unknown.add("mailbox")

        if self.knowledge_context is not None:
            knowledge = tuple(self.knowledge_context(normalized_project))
            fragments.extend(
                self._mapping_fragments(
                    source="knowledge",
                    rows=knowledge,
                    scope=scope,
                    provenance="knowledge-store",
                    unknown=unknown,
                )
            )
        else:
            unknown.add("knowledge")

        if selected_project is not None and not selected_project.current_state.strip():
            unknown.add(f"project_state:{selected_project.project_id}")
        if selected_project is not None and selected_project.status.value in {"active", "blocked"}:
            if not selected_project.updated_at.strip():
                unknown.add(f"project_updated_at:{selected_project.project_id}")

        manifest_id = _manifest_id(
            scope=scope,
            project_id=normalized_project,
            agent_role=normalized_agent,
            fragments=tuple(fragments),
            unknown_fields=tuple(sorted(unknown)),
        )
        return ContextManifest(
            manifest_id=manifest_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            scope=scope,
            project_id=normalized_project,
            fragments=tuple(fragments),
            unknown_fields=tuple(sorted(unknown)),
        )

    @staticmethod
    def _fragment(
        *,
        source: str,
        scope: ContextScope,
        content: str,
        provenance: str = "aurora-registry",
    ) -> ContextFragment:
        return ContextFragment(
            source=source,
            content=content,
            scope=scope,
            authoritative=True,
            provenance=provenance,
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def _mapping_fragments(
        cls,
        *,
        source: str,
        rows: tuple[Mapping[str, object], ...],
        scope: ContextScope,
        provenance: str,
        unknown: set[str],
    ) -> tuple[ContextFragment, ...]:
        if not rows:
            unknown.add(source)
            return ()
        fragments: list[ContextFragment] = []
        for index, row in enumerate(rows):
            rendered = _render_mapping(row, unknown)
            fragments.append(
                cls._fragment(
                    source=f"{source}[{index}]",
                    scope=scope,
                    content=rendered,
                    provenance=provenance,
                )
            )
        return tuple(fragments)


@dataclass(frozen=True)
class ContextGate:
    """Fail-closed gate that proves required authoritative context is present."""

    plane: AuroraContextPlane

    def prepare(
        self,
        *,
        scope: ContextScope,
        project_id: str | None = None,
        agent_role: str | None = None,
        task_id: str | None = None,
        require_mailbox: bool = True,
        require_knowledge: bool = True,
    ) -> ContextManifest:
        manifest = self.plane.build(
            scope=scope,
            project_id=project_id,
            agent_role=agent_role,
            task_id=task_id,
        )
        required = {"projects.catalog", "organization.catalog", "skills.catalog", "models.catalog"}
        if scope is ContextScope.PROJECT or project_id is not None:
            required.add("project")

        missing_required = {
            field
            for field in manifest.unknown_fields
            if field in {"project", "project_scope"}
            or (field == "mailbox" and require_mailbox)
            or (field == "knowledge" and require_knowledge)
            or field in required
        }
        if missing_required:
            raise AuroraContextError(
                "authoritative context is incomplete: "
                + ", ".join(sorted(missing_required))
            )
        return manifest


def _organization_catalog(organization: OrganizationRegistry) -> str:
    lines = []
    for role in sorted(organization.agents_by_role()):
        agent = organization.resolve_agent(role)
        lines.append(
            f"- {agent.role}: department={agent.department}; "
            f"mission={agent.mission}; "
            f"capabilities={', '.join(sorted(agent.capabilities)) or 'none'}; "
            f"skills={', '.join(agent.skill_ids) or 'none'}"
        )
    return "\n".join(lines) if lines else "No Aurora agents are registered."


def _model_catalog(
    models: tuple[ModelSpec, ...],
    model_registry: ModelRegistry | None = None,
) -> str:
    registered = model_registry.models if model_registry is not None else models
    if not registered:
        return "No inference models are registered."
    lines = []
    for model in sorted(registered, key=lambda item: item.id):
        lines.append(
            f"- {model.id}: provider={model.provider}; "
            f"enabled={model.enabled}; healthy={model.healthy}; available={model.available}; "
            f"capabilities={', '.join(model.capabilities) or 'none'}; "
            f"specialties={', '.join(model.specialties) or 'none'}; "
            f"context_window={model.context_window or 'unknown'}"
        )
    return "\n".join(lines)


def _project_detail(project: ProjectSpec) -> str:
    return "\n".join(
        (
            f"project_id={project.project_id}",
            f"name={project.name}",
            f"status={project.status.value}",
            f"objective={project.objective}",
            f"repository={project.repository or 'unknown'}",
            f"workspace={project.workspace or 'unknown'}",
            f"architecture_ref={project.architecture_ref or 'unknown'}",
            f"agents={', '.join(project.agent_roles) or 'none'}",
            f"skills={', '.join(project.skill_ids) or 'none'}",
            f"models={', '.join(project.model_ids) or 'none'}",
            f"tasks={', '.join(project.task_ids) or 'none'}",
            f"decisions={', '.join(project.decision_ids) or 'none'}",
            f"current_state={project.current_state or 'unknown'}",
            f"created_at={project.created_at or 'unknown'}",
            f"updated_at={project.updated_at or 'unknown'}",
        )
    )


def _render_mapping(row: Mapping[str, object], unknown: set[str]) -> str:
    lines = []
    for key in sorted(row):
        value = row[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            lines.append(f"{key}=unknown")
            unknown.add(f"{key}")
        elif isinstance(value, (dict, list, tuple)):
            lines.append(f"{key}={value}")
        else:
            lines.append(f"{key}={value}")
    return "\n".join(lines) or "unknown"


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned


def _manifest_id(
    *,
    scope: ContextScope,
    project_id: str | None,
    agent_role: str | None,
    fragments: tuple[ContextFragment, ...],
    unknown_fields: tuple[str, ...],
) -> str:
    import hashlib

    payload = "|".join(
        (
            scope.value,
            project_id or "",
            agent_role or "",
            *(f"{fragment.source}:{fragment.content}" for fragment in fragments),
            *unknown_fields,
        )
    )
    return "ctx-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
