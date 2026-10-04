import json
from pathlib import Path
from types import SimpleNamespace

from aurora.autonomy.internet import InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.workbench import UniversalWorkbench, WorkbenchRequest
from aurora.workbench.session import WorkSessionStore


class FakeGateway:
    policy = InternetPolicy(
        frozenset({"https"}),
        frozenset({"example.com"}),
    )


class FakeBroker:
    def resolve(self, request):
        return object() if request.name == "tool:rg" else None


class FakeCapabilityRuntime:
    def __init__(self):
        self.requests = []
        self.broker = FakeBroker()

    def execute(self, request, *, workspace, args=(), environment=None):
        self.requests.append((request, workspace, tuple(args), dict(environment or {})))
        capability = SimpleNamespace(
            artifact_id="artifact-tool",
            name=request.name,
            status="installed",
            verification="verified",
        )
        return SimpleNamespace(
            capability=capability,
            command=("/workspace/tool/bin/rg", *args),
            returncode=0,
            stdout="file.txt:match",
            stderr="",
            timed_out=False,
        )


def build(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sessions = WorkSessionStore(runtime / "sessions.db")
    runtime_exec = FakeCapabilityRuntime()
    wb = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=sessions,
        audit_path=runtime / "audit.jsonl",
        capability_runtime=runtime_exec,
    )
    return wb, runtime_exec, sessions, repo, runtime


def test_workbench_command_delegates_to_registered_capability_runtime(tmp_path: Path):
    wb, runtime_exec, sessions, _, runtime = build(tmp_path)

    result = wb.execute(
        WorkbenchRequest(
            "workbench:command",
            "engineer",
            "mission-code",
            reason="Search the isolated project",
            arguments=(json.dumps({
                "capability": "tool:rg",
                "args": ["pattern", "."],
            }),),
            correlation_id="corr-command",
        )
    )

    assert result.status == "succeeded"
    assert result.output["capability"] == "tool:rg"
    assert result.output["returncode"] == 0
    assert result.output["stdout"] == "file.txt:match"
    assert result.artifact_id == "artifact-tool"
    assert runtime_exec.requests[0][0].name == "tool:rg"
    assert runtime_exec.requests[0][0].actor == "engineer"
    assert runtime_exec.requests[0][0].reason == "Search the isolated project"
    assert runtime_exec.requests[0][2] == ("pattern", ".")
    assert sessions.get("mission-code").execution_receipts
    assert "corr-command" in sessions.get("mission-code").correlation_ids
    assert "receipt_id" in (runtime / "audit.jsonl").read_text(encoding="utf-8")


def test_workbench_command_rejects_arbitrary_executable_names(tmp_path: Path):
    wb, runtime_exec, _, _, _ = build(tmp_path)
    try:
        wb.execute(
            WorkbenchRequest(
                "workbench:command",
                "engineer",
                "mission-unsafe",
                arguments=(json.dumps({
                    "capability": "/bin/sh",
                    "args": ["-c", "touch /tmp/owned"],
                }),),
            )
        )
    except Exception as exc:
        assert "command capability is not registered" in str(exc)
    else:
        raise AssertionError("arbitrary executable requests must be rejected")
    assert runtime_exec.requests == []


def test_workbench_command_fails_closed_on_malformed_environment(tmp_path: Path):
    wb, _, _, _, _ = build(tmp_path)
    try:
        wb.execute(
            WorkbenchRequest(
                "workbench:command",
                "engineer",
                "mission-env",
                arguments=(json.dumps({
                    "capability": "tool:rg",
                    "args": [],
                    "environment": {"PATH": 123},
                }),),
            )
        )
    except Exception as exc:
        assert "environment must be a string map" in str(exc)
    else:
        raise AssertionError("malformed command environments must be rejected")
