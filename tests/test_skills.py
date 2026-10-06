from pathlib import Path

import pytest

from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.skills import SkillRegistry, SkillRegistryError


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_skill_registry_loads():
    registry = SkillRegistry.from_file(
        ROOT / "config" / "skills.json",
        configured_models={"nvidia/nemotron-3-ultra-550b-a55b","nvidia/nemotron-3-super-120b-a12b","z-ai/glm-5-3","deepseek-ai/deepseek-v4.1-flash","z-ai/glm-5-3-flash","google/gemma-4-31b-it","poolside/laguna-xs-2.1"},
    )
    assert "research-literature-analysis" in registry.ids()
    skill = registry.resolve("research-literature-analysis")
    assert skill.version == "1.0.0"
    assert skill.verification_requirements


def test_skill_rejects_missing_agent_capability(tmp_path: Path):
    path = tmp_path / "skills.json"
    path.write_text(
        '{"skills":[{"skill_id":"s","version":"1.0.0","name":"s","mission":"m",'
        '"instructions":"i","required_capabilities":["tool:curl"],'
        '"verification_requirements":[],"quality_checks":[],"dependencies":[]}]}',
        encoding="utf-8",
    )
    registry = SkillRegistry.from_file(path)
    with pytest.raises(SkillRegistryError, match="unauthorized capabilities"):
        registry.for_agent(
            role="agent",
            agent_capabilities=frozenset(),
            default_skill_ids=("s",),
        )


def test_skill_dependency_must_be_explicitly_present():
    registry = SkillRegistry(
        {
            "base": __import__("aurora.skills.models", fromlist=["SkillSpec"]).SkillSpec(
                "base", "1.0.0", "base", "m", "i", frozenset(), (), (), ()
            ),
            "advanced": __import__("aurora.skills.models", fromlist=["SkillSpec"]).SkillSpec(
                "advanced", "1.0.0", "advanced", "m", "i", frozenset(), (), (), (), ("base",)
            ),
        }
    )
    with pytest.raises(SkillRegistryError, match="requires skill dependency base"):
        registry.for_agent(
            role="agent",
            agent_capabilities=frozenset(),
            requested_skill_ids=("advanced",),
        )


def test_skill_system_context_is_rendered():
    registry = SkillRegistry(
        {
            "research": __import__("aurora.skills.models", fromlist=["SkillSpec"]).SkillSpec(
                "research",
                "1.0.0",
                "Research",
                "Find evidence",
                "Use primary evidence first.",
                frozenset(),
                ("source_check",),
                ("citation_check",),
            )
        }
    )
    assignment = registry.for_agent(
        role="researcher",
        agent_capabilities=frozenset(),
        requested_skill_ids=("research",),
    )
    rendered = assignment.render_system_context()
    assert "Use primary evidence first." in rendered
    assert "citation_check" in rendered


def test_skill_registry_validates_checked_in_organization():
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research",
                capabilities=frozenset({"tool:curl", "tool:rg"}),
                model_preferences=(),
                skill_ids=("research-literature-analysis",),
            )
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset({"tool:curl", "tool:rg"}),
            )
        },
    )
    registry = SkillRegistry.from_file(ROOT / "config" / "skills.json")
    registry.validate_organization(organization)
