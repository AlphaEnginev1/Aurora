from pathlib import Path

from aurora.autonomy.internet import InternetGateway, InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.organization import OrganizationRegistry
from aurora.workbench import UniversalWorkbench, WorkbenchRequest


class FakeGateway:
    timeout = 1.0

    def __init__(self):
        self.policy = InternetPolicy(frozenset({"https"}), frozenset({"api.github.com", "example.com"}))

    def authorize(self, url: str) -> None:
        return None


def test_workbench_scoped_file_operations_and_census(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "note.txt").write_text("hello Aurora", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()


    wb = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        configured_roots=(),
    )

    listed = wb.execute(WorkbenchRequest("workbench:file:list", "engineer", "m1", arguments=(".",)))
    assert listed.status == "succeeded"
    assert listed.output[0]["name"] == "note.txt"

    read = wb.execute(WorkbenchRequest("workbench:file:read", "researcher", "m1", arguments=("note.txt",)))
    assert read.output == "hello Aurora"
    assert read.artifact_id

    written = wb.execute(
        WorkbenchRequest("workbench:file:write", "engineer", "m1", arguments=("out.txt", "done"))
    )
    assert written.status == "succeeded"
    assert (repo / "out.txt").read_text(encoding="utf-8") == "done"

    intake = wb.execute(
        WorkbenchRequest("workbench:artifact:intake", "researcher", "m1", arguments=("note.txt",))
    )
    assert intake.artifact_id
    assert Path(intake.output["path"]).is_file()

    census = wb.execute(WorkbenchRequest("workbench:census", "director", "m1"))
    by_id = {item["capability_id"]: item for item in census.output}
    assert by_id["workbench:file"]["available"] is True
    assert by_id["workbench:artifact"]["available"] is True
    assert isinstance(by_id["workbench:github"]["available"], bool)


def test_all_configured_agents_have_universal_work_access():
    organization = OrganizationRegistry.from_file(
        "config/organization.json",
        configured_models={"meta/llama-3.1-8b-instruct"},
    )
    for agent in organization.agents.values():
        assert agent.universal_access is True
        assert organization.allows_capability(agent.role, "workbench:github:get") is True
        assert organization.allows_capability(agent.role, "tool:git") is True


def test_workbench_policy_fail_closed(tmp_path: Path):
    wb = UniversalWorkbench(
        repository_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        gateway=FakeGateway(),
        policy=Policy(frozenset(), frozenset()),
    )
    try:
        wb.execute(WorkbenchRequest("workbench:file:list", "agent", "m1", arguments=(".",)))
    except Exception as exc:
        assert "denied by policy" in str(exc)
    else:
        raise AssertionError("Workbench must fail closed when policy denies the capability")
