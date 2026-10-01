from pathlib import Path

import pytest

from aurora.projects import ProjectConfigError, ProjectRegistry, ProjectSpec, ProjectStatus


def test_project_registry_resolves_and_summarizes():
    registry = ProjectRegistry(
        {
            "project-a": ProjectSpec(
                project_id="project-a",
                name="Project A",
                objective="Build a governed research workflow.",
                status=ProjectStatus.ACTIVE,
                repository="https://github.com/example/project-a",
                agent_roles=("researcher",),
                skill_ids=("research",),
                model_ids=("model/a",),
                task_ids=("task-1",),
                decision_ids=("decision-1",),
                current_state="executing research phase",
            )
        }
    )

    project = registry.resolve("project-a")
    assert project.name == "Project A"
    assert registry.ids() == ("project-a",)
    assert registry.active() == (project,)
    assert registry.summary() == {
        "count": 1,
        "active_count": 1,
        "project_ids": ["project-a"],
    }
    assert "status=active" in registry.catalog()
    assert "state=executing research phase" in registry.catalog()


def test_project_registry_allows_empty_initial_catalog(tmp_path: Path):
    path = tmp_path / "projects.json"
    path.write_text('{"version":1,"projects":[]}', encoding="utf-8")

    registry = ProjectRegistry.from_file(path)

    assert registry.ids() == ()
    assert registry.active() == ()
    assert registry.catalog() == "No Aurora projects are registered."


def test_project_registry_rejects_duplicate_ids():
    with pytest.raises(ProjectConfigError, match="duplicate project_id"):
        ProjectRegistry(
            {
                "project-a": ProjectSpec(
                    "project-a", "Project A", "Objective", ProjectStatus.ACTIVE
                ),
                "project-b": ProjectSpec(
                    "project-a", "Project A2", "Objective 2", ProjectStatus.ACTIVE
                ),
            }
        )


def test_project_registry_rejects_unknown_references(tmp_path: Path):
    path = tmp_path / "projects.json"
    path.write_text(
        """
        {
          "projects": [{
            "project_id": "project-a",
            "name": "Project A",
            "objective": "Objective",
            "status": "active",
            "agent_roles": ["missing-agent"],
            "skill_ids": ["missing-skill"],
            "model_ids": ["missing-model"]
          }]
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(ProjectConfigError, match="unknown agent role"):
        ProjectRegistry.from_file(
            path,
            configured_agents={"researcher"},
            configured_skills={"research"},
            configured_models={"model/a"},
        )


def test_project_registry_deduplicates_configured_references():
    registry = ProjectRegistry(
        {
            "project-a": ProjectSpec(
                "project-a",
                "Project A",
                "Objective",
                ProjectStatus.PLANNED,
                agent_roles=("researcher", "researcher"),
                skill_ids=("research", "research"),
                model_ids=("model/a", "model/a"),
            )
        }
    )

    assert registry.resolve("project-a").agent_roles == ("researcher",)
