from pathlib import Path

import pytest

from aurora.autonomy.internet import InternetResponse
from aurora.workbench.github import GitHubConnector, GitHubConnectorError
from aurora.workbench.project_intake import ProjectIntakeError, ProjectIntakeService


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.policy = type("Policy", (), {"allowed_hosts": frozenset({"api.github.com"})})()

    def request_with_metadata(self, url, *, method="GET", headers=None, body=None):
        self.calls.append((url, method, headers or {}, body))
        return InternetResponse(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=b'{"ok":true,"name":"aurora","html_url":"https://github.com/AlphaEnginev1/Aurora"}',
        )


def test_github_connector_requires_explicit_write_policy(monkeypatch):
    gateway = FakeGateway()
    connector = GitHubConnector(gateway)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.delenv("AURORA_GITHUB_ALLOW_WRITES", raising=False)

    with pytest.raises(GitHubConnectorError, match="disabled by policy"):
        connector.create_branch("AlphaEnginev1", "Aurora", "test-branch", "abc123")


def test_github_connector_can_create_branch_and_pr_when_enabled(monkeypatch):
    gateway = FakeGateway()
    connector = GitHubConnector(gateway)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("AURORA_GITHUB_ALLOW_WRITES", "1")

    branch = connector.create_branch("AlphaEnginev1", "Aurora", "test-branch", "abc123")
    pr = connector.create_pull_request(
        "AlphaEnginev1", "Aurora", "Test PR", "test-branch", "main", "body"
    )

    assert branch["ok"] is True
    assert pr["ok"] is True
    assert gateway.calls[0][1] == "POST"
    assert "/git/refs" in gateway.calls[0][0]
    assert b'"ref": "refs/heads/test-branch"' in gateway.calls[0][3]
    assert gateway.calls[1][1] == "POST"
    assert gateway.calls[1][0].endswith("/pulls")


def test_github_merge_requires_explicit_merge_policy(monkeypatch):
    connector = GitHubConnector(FakeGateway())
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("AURORA_GITHUB_ALLOW_WRITES", "1")
    monkeypatch.delenv("AURORA_GITHUB_ALLOW_MERGE", raising=False)

    with pytest.raises(GitHubConnectorError, match="merge is disabled"):
        connector.merge_pull_request("AlphaEnginev1", "Aurora", 1)


def test_project_intake_inspects_local_project(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    git_config = tmp_path / ".git" / "config"
    git_config.parent.mkdir()
    git_config.write_text("[remote \"origin\"]\n\turl = git@github.com:AlphaEnginev1/Aurora.git\n", encoding="utf-8")

    service = ProjectIntakeService(
        projects_path=tmp_path / "projects.json",
        graph_path=tmp_path / "graph.json",
    )
    report = service.inspect_local(tmp_path, project_id="demo")

    assert report.name == tmp_path.name
    assert report.file_count == 4
    assert "python" in report.technologies
    assert "node" in report.technologies
    assert report.repository == "https://github.com/AlphaEnginev1/Aurora"
    assert report.sources[0].source_type == "local"


def test_project_intake_registers_and_rejects_duplicates(tmp_path: Path):
    service = ProjectIntakeService(
        projects_path=tmp_path / "projects.json",
        graph_path=tmp_path / "graph.json",
    )
    project = service.register(
        project_id="aurora",
        name="Aurora",
        objective="Build Aurora",
        workspace=str(tmp_path),
        repository="https://github.com/AlphaEnginev1/Aurora",
    )
    assert project.project_id == "aurora"
    assert (tmp_path / "projects.json").is_file()
    assert len(service.graph.for_project("aurora")) == 2

    with pytest.raises(ProjectIntakeError, match="already exists"):
        service.register(
            project_id="aurora",
            name="Aurora",
            objective="Duplicate",
        )
