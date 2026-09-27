from pathlib import Path

import pytest

from aurora.organization import OrganizationConfigError, OrganizationRegistry


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_organization_registry_loads_and_resolves():
    registry = OrganizationRegistry.from_file(
        ROOT / "config" / "organization.json",
        configured_models={"meta/llama-3.1-8b-instruct"},
    )

    researcher = registry.resolve_agent("researcher")
    assert researcher.department == "research"
    assert registry.department_for("researcher").department_id == "research"
    assert registry.model_preferences_for("researcher") == (
        "meta/llama-3.1-8b-instruct",
    )


def test_organization_rejects_unknown_role(tmp_path: Path):
    path = tmp_path / "organization.json"
    path.write_text(
        '{"departments":[{"department_id":"d","mission":"m","agent_ids":["a"],'
        '"model_preferences":[],"allowed_capabilities":[]}],'
        '"agents":[{"agent_id":"a","role":"agent","department":"d","mission":"m",'
        '"capabilities":[],"model_preferences":[]}]}',
        encoding="utf-8",
    )
    registry = OrganizationRegistry.from_file(path)
    with pytest.raises(OrganizationConfigError, match="unknown Aurora agent role"):
        registry.resolve_agent("missing")


def test_organization_rejects_undeclared_capability(tmp_path: Path):
    path = tmp_path / "organization.json"
    path.write_text(
        '{"departments":[{"department_id":"d","mission":"m","agent_ids":["a"],'
        '"model_preferences":[],"allowed_capabilities":[]}],'
        '"agents":[{"agent_id":"a","role":"agent","department":"d","mission":"m",'
        '"capabilities":[],"model_preferences":[]}]}',
        encoding="utf-8",
    )
    registry = OrganizationRegistry.from_file(path)
    with pytest.raises(OrganizationConfigError, match="not authorized"):
        registry.validate_task(role="agent", capability="tool:curl")


def test_organization_rejects_model_not_in_inference_policy(tmp_path: Path):
    path = tmp_path / "organization.json"
    path.write_text(
        '{"departments":[{"department_id":"d","mission":"m","agent_ids":["a"],'
        '"model_preferences":["unknown"],"allowed_capabilities":[]}],'
        '"agents":[{"agent_id":"a","role":"agent","department":"d","mission":"m",'
        '"capabilities":[],"model_preferences":["unknown"]}]}',
        encoding="utf-8",
    )
    with pytest.raises(OrganizationConfigError, match="model not present"):
        OrganizationRegistry.from_file(path, configured_models={"configured"})
