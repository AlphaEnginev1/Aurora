from aurora.context import (
    AuroraContextError,
    AuroraContextPlane,
    ContextGate,
    ContextScope,
)
from aurora.inference.models import ModelSpec
from aurora.inference.registry import ModelRegistry
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.projects import ProjectRegistry, ProjectSpec, ProjectStatus
from aurora.skills import SkillRegistry
from aurora.skills.models import SkillSpec


def _fixtures():
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="Research",
                capabilities=frozenset({"reasoning"}),
                model_preferences=("model/research",),
                skill_ids=("research",),
            )
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="Research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset({"reasoning"}),
            )
        },
    )
    skills = SkillRegistry(
        {
            "research": SkillSpec(
                "research",
                "1.0.0",
                "Research",
                "Research using verified evidence.",
                "Use verified evidence.",
                frozenset(),
                ("source_check",),
                ("citation_check",),
                ("research",),
            )
        }
    )
    projects = ProjectRegistry(
        {
            "project-a": ProjectSpec(
                "project-a",
                "Project A",
                "Investigate a research question.",
                ProjectStatus.ACTIVE,
                repository="https://github.com/example/project-a",
                architecture_ref="docs/architecture.md",
                agent_roles=("researcher",),
                skill_ids=("research",),
                model_ids=("model/research",),
                current_state="research phase",
                updated_at="2026-10-01T12:00:00+00:00",
            )
        }
    )
    models = (
        ModelSpec(
            "model/research",
            "test-provider",
            "https://example.invalid/model",
            capabilities=("reasoning",),
            specialties=("research",),
        ),
    )
    return projects, organization, skills, models


def test_context_plane_builds_authoritative_manifest():
    projects, organization, skills, models = _fixtures()
    plane = AuroraContextPlane(projects, organization, skills, models)
    manifest = plane.build(
        scope=ContextScope.PROJECT,
        project_id="project-a",
        agent_role="researcher",
    )

    assert manifest.project_id == "project-a"
    assert manifest.manifest_id.startswith("ctx-")
    assert "projects.catalog" in {fragment.source for fragment in manifest.fragments}
    assert "projects.project-a" in {fragment.source for fragment in manifest.fragments}
    assert "organization.agent.researcher" in {fragment.source for fragment in manifest.fragments}
    assert "models.catalog" in {fragment.source for fragment in manifest.fragments}
    assert "project_state:project-a" not in manifest.unknown_fields


def test_context_gate_fails_closed_when_global_mailbox_is_required():
    projects, organization, skills, models = _fixtures()
    gate = ContextGate(AuroraContextPlane(projects, organization, skills, models))

    try:
        gate.prepare(scope=ContextScope.DIRECTOR)
    except AuroraContextError as exc:
        assert "mailbox" in str(exc)
    else:
        raise AssertionError("expected incomplete context to fail closed")


def test_context_gate_accepts_optional_global_sources_when_disabled():
    projects, organization, skills, models = _fixtures()
    plane = AuroraContextPlane(
        projects,
        organization,
        skills,
        models,
        runtime_context=lambda _: {"runtime": "unknown"},
        mailbox_context=lambda _: [{"latest": "none"}],
        knowledge_context=lambda _: [{"knowledge": "verified-index"}],
    )
    manifest = ContextGate(plane).prepare(
        scope=ContextScope.DIRECTOR,
        require_mailbox=False,
        require_knowledge=False,
    )

    assert "mailbox" not in manifest.unknown_fields
    assert "knowledge" not in manifest.unknown_fields
    assert any(fragment.source == "mailbox[0]" for fragment in manifest.fragments)


def test_project_scope_requires_registered_project():
    projects, organization, skills, models = _fixtures()
    gate = ContextGate(AuroraContextPlane(projects, organization, skills, models))

    try:
        gate.prepare(scope=ContextScope.PROJECT, project_id="missing")
    except AuroraContextError as exc:
        assert "unknown Aurora project" in str(exc)
    else:
        raise AssertionError("expected missing project to fail")


def test_context_plane_reads_current_model_registry_catalog():
    projects, organization, skills, models = _fixtures()
    model_registry = ModelRegistry(models)
    plane = AuroraContextPlane(
        projects,
        organization,
        skills,
        models,
        model_registry=model_registry,
    )

    model_registry.register(
        ModelSpec(
            "model/second",
            "test-provider",
            "https://example.invalid/second",
            capabilities=("reasoning",),
        )
    )

    manifest = plane.build(scope=ContextScope.GLOBAL)
    model_catalog = next(
        fragment.content
        for fragment in manifest.fragments
        if fragment.source == "models.catalog"
    )
    assert "model/second" in model_catalog
