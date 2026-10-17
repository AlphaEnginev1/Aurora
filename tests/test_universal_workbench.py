from pathlib import Path
import json

import pytest

from aurora.autonomy.internet import InternetGateway, InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.organization import OrganizationRegistry
from aurora.workbench import UniversalWorkbench, WorkbenchError, WorkbenchRequest
from aurora.workbench.session import WorkSessionStore


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


    sessions = WorkSessionStore(runtime / "sessions.db")
    wb = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=sessions,
        audit_path=runtime / "audit.jsonl",
        configured_roots=(),
    )

    listed = wb.execute(
        WorkbenchRequest(
            "workbench:file:list", "engineer", "m1", arguments=(".",), correlation_id="corr-list"
        )
    )
    assert listed.status == "succeeded"
    assert listed.receipt_id
    assert listed.correlation_id == "corr-list"
    assert any(item["name"] == "note.txt" for item in listed.output)

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

    preflight = wb.execute(WorkbenchRequest("workbench:preflight", "director", "m1"))
    assert preflight.status == "succeeded"
    assert preflight.output["critical_ready"] is True
    assert set(preflight.output["required_capabilities"]) == {
        "workbench:file",
        "workbench:artifact",
        "workbench:document",
    }

    census = wb.execute(WorkbenchRequest("workbench:census", "director", "m1"))
    by_id = {item["capability_id"]: item for item in census.output}
    assert by_id["workbench:file"]["available"] is True
    assert by_id["workbench:artifact"]["available"] is True
    assert isinstance(by_id["workbench:github"]["available"], bool)

    session = sessions.get("m1")
    assert session.execution_receipts
    assert "corr-list" in session.correlation_ids
    assert "workbench:file:read" in session.capability_ids
    assert session.artifact_ids
    assert sessions.get_receipts("m1")
    assert "receipt_id" in (runtime / "audit.jsonl").read_text(encoding="utf-8")


def test_all_configured_agents_have_universal_work_access():
    organization = OrganizationRegistry.from_file(
        "config/organization.json",
        configured_models={"nvidia/nemotron-3-ultra-550b-a55b","nvidia/nemotron-3-super-120b-a12b","z-ai/glm-5-3","deepseek-ai/deepseek-v4.1-flash","z-ai/glm-5-3-flash","google/gemma-4-31b-it","poolside/laguna-xs-2.1"},
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


def test_work_session_store_reads_legacy_payload_with_new_defaults(tmp_path: Path):
    path = tmp_path / "legacy.db"
    legacy = (
        '{"work_id":"legacy-work","mission_id":"legacy-mission","actor":"agent",'
        '"environment":"local","workspace_root":"%s","state":"active",'
        '"capability_ids":[],"resources":{},"created_at":"2026-01-01T00:00:00+00:00",'
        '"updated_at":"2026-01-01T00:00:00+00:00"}'
    ) % tmp_path
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE work_sessions (work_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE work_session_receipts ("
            "receipt_id TEXT PRIMARY KEY, work_id TEXT NOT NULL, correlation_id TEXT,"
            "created_at TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        db.execute("INSERT INTO work_sessions(work_id,payload) VALUES (?,?)", ("legacy-work", legacy))
    loaded = WorkSessionStore(path).get("legacy-work")
    assert loaded.work_id == "legacy-work"
    assert loaded.execution_receipts == ()
    assert loaded.approval_state == "not_required"


def test_workbench_replays_completed_side_effect_receipt_without_running_it_twice(
    tmp_path: Path, monkeypatch
):
    from aurora.workbench.models import WorkbenchResult

    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sessions = WorkSessionStore(runtime / "sessions.db")
    wb = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=sessions,
        audit_path=runtime / "audit.jsonl",
        configured_roots=(),
    )
    executions = []

    def fake_file_write(request):
        executions.append(tuple(request.arguments))
        return WorkbenchResult(
            capability=request.capability,
            operation="file_write",
            status="succeeded",
            output={"path": request.arguments[0], "content": request.arguments[1]},
            provenance="controlled-write",
        )

    monkeypatch.setattr(wb, "_file_write", fake_file_write)
    request = WorkbenchRequest(
        capability="workbench:file:write",
        actor="engineer",
        mission_id="mission-idempotent-write",
        reason="write one controlled artifact",
        arguments=("result.txt", "durable output"),
        options={"work_id": "mission-idempotent-write"},
        correlation_id="corr-idempotent-write",
        idempotency_key="task:mission-idempotent-write:write-result",
    )

    first = wb.execute(request)
    replay = wb.execute(request)

    assert executions == [("result.txt", "durable output")]
    assert replay.output == first.output
    assert replay.receipt_id == first.receipt_id
    assert replay.correlation_id == first.correlation_id
    action = sessions.get_action("task:mission-idempotent-write:write-result")
    assert action is not None
    assert action["state"] == "completed"
    assert action["result"]["receipt_id"] == first.receipt_id


def test_workbench_rejects_reusing_action_key_for_different_side_effect(
    tmp_path: Path, monkeypatch
):
    from aurora.workbench.models import WorkbenchResult

    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sessions = WorkSessionStore(runtime / "sessions.db")
    wb = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=sessions,
        audit_path=runtime / "audit.jsonl",
        configured_roots=(),
    )
    executions = []

    def fake_file_write(request):
        executions.append(tuple(request.arguments))
        return WorkbenchResult(
            request.capability,
            "file_write",
            "succeeded",
            output={"path": request.arguments[0]},
            provenance="controlled-write",
        )

    monkeypatch.setattr(wb, "_file_write", fake_file_write)
    first_request = WorkbenchRequest(
        "workbench:file:write",
        "engineer",
        "mission-key-binding",
        arguments=("one.txt", "first"),
        options={"work_id": "mission-key-binding"},
        idempotency_key="same-key-different-payload",
    )
    wb.execute(first_request)
    second_request = WorkbenchRequest(
        "workbench:file:write",
        "engineer",
        "mission-key-binding",
        arguments=("two.txt", "second"),
        options={"work_id": "mission-key-binding"},
        idempotency_key="same-key-different-payload",
    )

    try:
        wb.execute(second_request)
    except Exception as exc:
        assert "idempotency_key is already bound to a different Workbench action" in str(exc)
    else:
        raise AssertionError("Workbench executed two different side effects under one action key")

    assert executions == [("one.txt", "first")]


def test_workbench_does_not_repeat_side_effect_with_indeterminate_prior_outcome(
    tmp_path: Path, monkeypatch
):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from aurora.workbench.models import WorkbenchResult

    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sessions = WorkSessionStore(runtime / "sessions.db")
    wb = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=sessions,
        audit_path=runtime / "audit.jsonl",
        configured_roots=(),
    )
    request = WorkbenchRequest(
        "workbench:file:write",
        "engineer",
        "mission-crashed-write",
        arguments=("side-effect.txt", "must not repeat"),
        options={"work_id": "mission-crashed-write"},
        idempotency_key="task:mission-crashed-write:file-write",
    )
    sessions.create_or_get(
        work_id="mission-crashed-write",
        mission_id="mission-crashed-write",
        actor="engineer",
        environment="local",
        workspace_root=str(repo),
    )
    decision = sessions.begin_action(
        idempotency_key=request.idempotency_key,
        work_id="mission-crashed-write",
        mission_id="mission-crashed-write",
        capability_id=request.capability,
        request_fingerprint=wb._idempotency_fingerprint(request, request.capability),
        replay_safe=False,
    )
    assert decision["state"] == "claimed"

    stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with sqlite3.connect(sessions.path) as db:
        db.execute(
            "UPDATE work_session_actions SET updated_at = ? WHERE idempotency_key = ?",
            (stale_time, request.idempotency_key),
        )

    executions = []

    def fake_file_write(_request):
        executions.append("executed")
        return WorkbenchResult(
            "workbench:file:write",
            "file_write",
            "succeeded",
            output={"unexpected": True},
            provenance="controlled-write",
        )

    monkeypatch.setattr(wb, "_file_write", fake_file_write)
    try:
        wb.execute(request)
    except Exception as exc:
        assert "outcome is unknown" in str(exc)
        assert "side effect was not repeated" in str(exc)
    else:
        raise AssertionError("Workbench silently re-executed an indeterminate side effect")

    assert executions == []
    state = sessions.get_action(request.idempotency_key)
    assert state is not None
    assert state["state"] == "indeterminate"



def test_project_scoped_workbench_paths_cannot_target_another_project(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    project_a = tmp_path / "project-a"
    project_a.mkdir()
    project_b = tmp_path / "project-b"
    project_b.mkdir()
    sessions = WorkSessionStore(runtime / "sessions.db")
    wb = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=sessions,
        configured_roots=(project_a, project_b),
    )
    wb.project_intake.register(
        project_id="project-a",
        name="Project A",
        objective="Keep Project A file access isolated",
        workspace=str(project_a),
    )
    wb.project_intake.register(
        project_id="project-b",
        name="Project B",
        objective="Separate Project B from Project A",
        workspace=str(project_b),
    )

    with pytest.raises(WorkbenchError, match="outside project-scoped workspace"):
        wb.execute(
            WorkbenchRequest(
                "workbench:project:inspect",
                "director",
                "mission-project-a-inspect",
                arguments=(str(project_b),),
                options={"project_id": "project-a"},
            )
        )

    inspect_own = wb.execute(
        WorkbenchRequest(
            "workbench:project:inspect",
            "director",
            "mission-project-a-inspect-own",
            arguments=(str(project_a),),
            options={"project_id": "project-a"},
        )
    )
    assert inspect_own.status == "succeeded"
    assert inspect_own.output["path"] == str(project_a)

    with pytest.raises(WorkbenchError, match="outside project-scoped workspace"):
        wb.execute(
            WorkbenchRequest(
                "workbench:file:write",
                "director",
                "mission-project-a",
                arguments=(str(project_b / "must-not-write.txt"), "wrong project"),
                options={"project_id": "project-a"},
            )
        )
    assert not (project_b / "must-not-write.txt").exists()

    written = wb.execute(
        WorkbenchRequest(
            "workbench:file:write",
            "director",
            "mission-project-a",
            arguments=("safe.txt", "correct project"),
            options={"project_id": "project-a"},
        )
    )
    assert written.status == "succeeded"
    assert (project_a / "safe.txt").read_text(encoding="utf-8") == "correct project"

    # A project-scoped command must use the same workspace boundary, even if
    # its JSON payload tries to nominate a different working directory.
    wb.capability_runtime = object()
    command = WorkbenchRequest(
        "workbench:command",
        "director",
        "mission-project-a-command",
        arguments=(json.dumps({
            "capability": "test.capability",
            "args": [],
            "workspace": str(project_b),
        }),),
        options={"project_id": "project-a"},
    )
    with pytest.raises(WorkbenchError, match="outside project-scoped workspace"):
        wb._command_execute(command)



def test_project_scoped_github_operations_cannot_target_another_repository(
    tmp_path: Path,
    monkeypatch,
):
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    workbench = UniversalWorkbench(
        repository_root=repository_root,
        runtime_root=runtime_root,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=WorkSessionStore(runtime_root / "sessions.db"),
        configured_roots=(),
    )
    workbench.project_intake.register(
        project_id="project-a",
        name="Project A",
        objective="Bind GitHub actions to the project repository",
        workspace=str(repository_root),
        repository="https://github.com/acme/project-a",
    )

    connector_calls = []
    def record_call(name):
        def call(*args, **kwargs):
            connector_calls.append((name, args, kwargs))
            return {"ok": True, "merged": True}
        return call

    for method in (
        "create_branch",
        "upsert_file",
        "create_pull_request",
        "merge_pull_request",
        "workflow_runs",
        "get",
    ):
        setattr(workbench.github, method, record_call(method))

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("AURORA_GITHUB_ALLOW_WRITES", "1")
    monkeypatch.setenv("AURORA_GITHUB_ALLOW_MERGE", "1")

    cases = [
        (
            "workbench:github:branch",
            (json.dumps({"owner": "acme", "repo": "project-b", "branch": "feature", "sha": "abc"}),),
        ),
        (
            "workbench:github:file:write",
            (json.dumps({"owner": "acme", "repo": "project-b", "path": "README.md", "content": "wrong repo", "message": "write", "branch": "main"}),),
        ),
        (
            "workbench:github:pr",
            (json.dumps({"owner": "acme", "repo": "project-b", "title": "wrong repo", "head": "feature", "base": "main"}),),
        ),
        (
            "workbench:github:merge",
            (json.dumps({"owner": "acme", "repo": "project-b", "number": 7}),),
        ),
        (
            "workbench:github:workflow:read",
            (json.dumps({"owner": "acme", "repo": "project-b"}),),
        ),
        ("workbench:github:get", ("repo:acme/project-b",)),
    ]
    for capability, arguments in cases:
        with pytest.raises(WorkbenchError, match="conflicts with canonical repository"):
            workbench.execute(
                WorkbenchRequest(
                    capability,
                    "director",
                    "mission-project-a",
                    arguments=arguments,
                    options={"project_id": "project-a"},
                )
            )

    assert connector_calls == []

    valid = workbench.execute(
        WorkbenchRequest(
            "workbench:github:file:write",
            "director",
            "mission-project-a-valid",
            arguments=(json.dumps({
                "owner": "acme",
                "repo": "project-a",
                "path": "README.md",
                "content": "valid project",
                "message": "write",
                "branch": "main",
            }),),
            options={"project_id": "project-a"},
        )
    )
    assert valid.status == "succeeded"
    assert len(connector_calls) == 1
    assert connector_calls[0][0] == "upsert_file"
