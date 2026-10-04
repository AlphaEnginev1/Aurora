"""Project intake and multi-source project graph for Aurora Workbench."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aurora.projects.registry import ProjectConfigError, ProjectSpec, ProjectStatus


class ProjectIntakeError(RuntimeError):
    """Raised when project discovery or registration cannot be completed safely."""


@dataclass(frozen=True)
class ProjectSource:
    source_id: str
    source_type: str
    locator: str
    project_id: str
    discovered_at: str


@dataclass(frozen=True)
class ProjectIntakeReport:
    path: str
    name: str
    file_count: int
    directories: int
    manifests: tuple[str, ...]
    technologies: tuple[str, ...]
    repository: str | None
    sources: tuple[ProjectSource, ...]


class ProjectSourceGraph:
    """Durable source graph stored separately from the canonical project catalog."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"version": 1, "sources": []})

    def add(self, source: ProjectSource) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        sources = data.setdefault("sources", [])
        if not any(item.get("source_id") == source.source_id for item in sources):
            sources.append(asdict(source))
            self._write(data)

    def for_project(self, project_id: str) -> tuple[ProjectSource, ...]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return tuple(
            ProjectSource(**item)
            for item in data.get("sources", [])
            if item.get("project_id") == project_id
        )

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


class ProjectIntakeService:
    """Discover local projects and register canonical project metadata."""

    def __init__(
        self,
        *,
        projects_path: str | Path,
        graph_path: str | Path,
        github=None,
    ) -> None:
        self.projects_path = Path(projects_path)
        self.projects_path.parent.mkdir(parents=True, exist_ok=True)
        self.graph = ProjectSourceGraph(graph_path)
        self.github = github

    def inspect_local(self, path: str | Path, *, project_id: str = "unregistered") -> ProjectIntakeReport:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ProjectIntakeError(f"project path is not a directory: {root}")
        files = 0
        directories = 0
        manifests: list[str] = []
        technologies: set[str] = set()
        repository = self._git_remote(root)

        important = {
            "README.md": "documentation",
            "pyproject.toml": "python",
            "package.json": "node",
            "Cargo.toml": "rust",
            "go.mod": "go",
            "requirements.txt": "python",
            "pom.xml": "java",
            "build.gradle": "java",
            "Dockerfile": "docker",
            "docker-compose.yml": "docker",
            "docker-compose.yaml": "docker",
        }
        for item in root.rglob("*"):
            if ".git" in item.parts or ".venv" in item.parts or "__pycache__" in item.parts:
                continue
            if item.is_dir():
                directories += 1
                continue
            files += 1
            if item.name in important:
                manifests.append(str(item.relative_to(root)))
                technologies.add(important[item.name])
            elif item.suffix.lower() in {".py", ".pyi"}:
                technologies.add("python")
            elif item.suffix.lower() in {".ts", ".tsx", ".js", ".jsx"}:
                technologies.add("javascript/typescript")
            elif item.suffix.lower() == ".rs":
                technologies.add("rust")
            elif item.suffix.lower() == ".go":
                technologies.add("go")

        now = datetime.now(timezone.utc).isoformat()
        source = ProjectSource(
            source_id=f"local:{project_id}:{hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:12]}",
            source_type="local",
            locator=str(root),
            project_id=project_id,
            discovered_at=now,
        )
        self.graph.add(source)
        return ProjectIntakeReport(
            path=str(root),
            name=root.name,
            file_count=files,
            directories=directories,
            manifests=tuple(sorted(set(manifests))),
            technologies=tuple(sorted(technologies)),
            repository=repository,
            sources=self.graph.for_project(project_id),
        )

    def register(
        self,
        *,
        project_id: str,
        name: str,
        objective: str,
        workspace: str | None = None,
        repository: str | None = None,
        status: str = "active",
    ) -> ProjectSpec:
        values = {
            "project_id": project_id.strip(),
            "name": name.strip(),
            "objective": objective.strip(),
            "status": status.strip().lower(),
            "workspace": workspace.strip() if isinstance(workspace, str) and workspace.strip() else None,
            "repository": repository.strip() if isinstance(repository, str) and repository.strip() else None,
        }
        required = ("project_id", "name", "objective", "status")
        if not all(values[key] for key in required):
            raise ProjectIntakeError("project_id, name, objective and status are required")
        try:
            project_status = ProjectStatus(values["status"])
        except ValueError as exc:
            raise ProjectIntakeError(f"invalid project status: {status}") from exc

        data = self._load_projects()
        projects = data.setdefault("projects", [])
        if any(item.get("project_id") == values["project_id"] for item in projects):
            raise ProjectIntakeError(f"project already exists: {values['project_id']}")
        now = datetime.now(timezone.utc).isoformat()
        item = {
            **values,
            "agent_roles": [],
            "skill_ids": [],
            "model_ids": [],
            "task_ids": [],
            "decision_ids": [],
            "current_state": "registered by Universal Workbench intake",
            "created_at": now,
            "updated_at": now,
        }
        projects.append(item)
        self._save_projects(data)
        project = ProjectSpec(
            project_id=values["project_id"],
            name=values["name"],
            objective=values["objective"],
            status=project_status,
            repository=values["repository"],
            workspace=values["workspace"],
            current_state=item["current_state"],
            created_at=now,
            updated_at=now,
        )
        if workspace:
            self.graph.add(ProjectSource(
                source_id=f"local:{project.project_id}:{hashlib.sha256(workspace.encode('utf-8')).hexdigest()[:12]}",
                source_type="local",
                locator=workspace,
                project_id=project.project_id,
                discovered_at=now,
            ))
        if repository:
            self.graph.add(ProjectSource(
                source_id=f"github:{project.project_id}:{repository}",
                source_type="github",
                locator=repository,
                project_id=project.project_id,
                discovered_at=now,
            ))
        return project

    def register_github(
        self,
        *,
        project_id: str,
        name: str | None,
        objective: str,
        owner: str,
        repo: str,
    ) -> ProjectSpec:
        if self.github is None:
            raise ProjectIntakeError("GitHub connector is not configured")
        metadata = self.github.get(self.github.repo_path(owner, repo))
        canonical = str(metadata.get("html_url") or f"https://github.com/{owner}/{repo}")
        resolved_name = (name or str(metadata.get("name") or repo)).strip()
        return self.register(
            project_id=project_id,
            name=resolved_name,
            objective=objective,
            repository=canonical,
        )

    def _load_projects(self) -> dict[str, Any]:
        if not self.projects_path.exists():
            return {"version": 1, "projects": []}
        try:
            data = json.loads(self.projects_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectIntakeError(f"invalid project registry: {self.projects_path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
            raise ProjectIntakeError("project registry must contain a projects list")
        return data

    def _save_projects(self, data: dict[str, Any]) -> None:
        tmp = self.projects_path.with_suffix(self.projects_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.projects_path)

    @staticmethod
    def _git_remote(root: Path) -> str | None:
        config = root / ".git" / "config"
        if not config.is_file():
            return None
        for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("url = "):
                value = line[6:].strip()
                value = re.sub(r"^git@", "https://", value).replace(":", "/", 1)
                return value.removesuffix(".git")
        return None


__all__ = ["ProjectIntakeError", "ProjectIntakeReport", "ProjectIntakeService", "ProjectSource", "ProjectSourceGraph"]
