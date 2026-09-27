from pathlib import Path

import pytest

from aurora.autonomy.acquisition import AcquisitionResult
from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.capability_runtime import (
    CapabilityExecutionError,
    CapabilityRuntime,
)
from aurora.autonomy.models import CapabilityRequest
from aurora.autonomy.policy import Policy
from aurora.autonomy.registry import ToolSpec
from aurora.autonomy.sandbox import SandboxResult


class FakeAcquisition:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def acquire(self, spec):
        self.calls.append(spec.name)
        return self.result


class FakeSandbox:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, command, *, workspace, environment=None):
        self.calls.append((tuple(command), Path(workspace), environment))
        return self.result


def test_runtime_executes_only_registered_system_executable(tmp_path):
    executable = tmp_path / "tool"
    executable.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    spec = ToolSpec("tool", "system", "ubuntu-apt", "tool")
    acquisition = FakeAcquisition(
        AcquisitionResult(
            "tool",
            "installed",
            "ubuntu-apt",
            "tool",
            str(executable),
            sha256="a" * 64,
            verification="verified",
        )
    )
    sandbox = FakeSandbox(SandboxResult(("tool", "hello"), 0, "ok\n", ""))
    broker = CapabilityBroker(
        Policy(frozenset({"tool:tool"}), frozenset({"ubuntu-apt"})),
        {"tool": spec},
        acquisition,
    )
    runtime = CapabilityRuntime(broker, sandbox)

    result = runtime.execute(
        CapabilityRequest("tool:tool", "agent-1", "run tool"),
        workspace=tmp_path,
        args=("hello",),
    )

    assert result.returncode == 0
    assert result.stdout == "ok\n"
    assert result.command == (str(executable), "hello")
    assert sandbox.calls[0][0] == (str(executable), "hello")
    assert acquisition.calls == ["tool"]


def test_runtime_refuses_unverified_capability(tmp_path):
    executable = tmp_path / "tool"
    executable.write_text("tool", encoding="utf-8")
    acquisition = FakeAcquisition(
        AcquisitionResult(
            "tool",
            "installed",
            "ubuntu-apt",
            "tool",
            str(executable),
            sha256=None,
            verification="observed",
        )
    )
    broker = CapabilityBroker(
        Policy(frozenset({"tool:tool"}), frozenset({"ubuntu-apt"})),
        {"tool": ToolSpec("tool", "system", "ubuntu-apt", "tool")},
        acquisition,
    )
    runtime = CapabilityRuntime(broker, FakeSandbox(SandboxResult(("tool",), 0, "", "")))

    with pytest.raises(CapabilityExecutionError, match="not verified"):
        runtime.execute(
            CapabilityRequest("tool:tool", "agent-1"),
            workspace=tmp_path,
        )


def test_runtime_maps_pypi_executable_to_sandbox_stable_path(tmp_path):
    install_root = tmp_path / "managed-site"
    executable = install_root / "bin" / "normalizer"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    spec = ToolSpec("normalizer", "runtime", "pypi", "normalizer", version="1.0")
    acquisition = FakeAcquisition(
        AcquisitionResult(
            "normalizer",
            "installed",
            "pypi",
            "normalizer",
            str(install_root),
            version="1.0",
            artifact_sha256="b" * 64,
            verification="verified",
        )
    )
    sandbox = FakeSandbox(
        SandboxResult(("/workspace/managed-site/bin/normalizer", "--help"), 0, "help\n", "")
    )
    broker = CapabilityBroker(
        Policy(frozenset({"python.package:normalizer"}), frozenset({"pypi"})),
        {"normalizer": spec},
        acquisition,
    )

    result = CapabilityRuntime(broker, sandbox).execute(
        CapabilityRequest("python.package:normalizer", "agent-1"),
        workspace=tmp_path,
        args=("--help",),
    )

    assert result.command == ("/workspace/managed-site/bin/normalizer", "--help")


def test_runtime_rejects_pypi_install_outside_workspace(tmp_path):
    install_root = tmp_path / "outside" / "managed-site"
    install_root.mkdir(parents=True)
    (install_root / "bin").mkdir()
    (install_root / "bin" / "normalizer").write_text("x", encoding="utf-8")

    acquisition = FakeAcquisition(
        AcquisitionResult(
            "normalizer",
            "installed",
            "pypi",
            "normalizer",
            str(install_root),
            verification="verified",
        )
    )
    broker = CapabilityBroker(
        Policy(frozenset({"python.package:normalizer"}), frozenset({"pypi"})),
        {"normalizer": ToolSpec("normalizer", "runtime", "pypi", "normalizer")},
        acquisition,
    )

    with pytest.raises(CapabilityExecutionError, match="directly under"):
        CapabilityRuntime(broker, FakeSandbox(SandboxResult(("x",), 0, "", ""))).execute(
            CapabilityRequest("python.package:normalizer", "agent-1"),
            workspace=tmp_path,
        )
