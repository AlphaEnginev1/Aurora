import json
from pathlib import Path

from aurora.autonomy.capability_resolution import CapabilityResolution
from aurora.autonomy.models import CapabilityRequest, CapabilityResult
from aurora.autonomy.internet import InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.workbench import UniversalWorkbench, WorkbenchRequest
from aurora.workbench.session import WorkSessionStore


class FakeGateway:
    timeout = 1.0

    def __init__(self):
        self.policy = InternetPolicy(
            frozenset({"https"}),
            frozenset({"api.github.com", "example.com"}),
        )


class FakeResolution:
    def __init__(self, status="installed"):
        self.status = status
        self.calls = []

    def ensure(self, request: CapabilityRequest) -> CapabilityResolution:
        self.calls.append(request)
        result = CapabilityResult(
            name=request.name,
            status=self.status,
            version="1.2" if self.status == "installed" else None,
            source="pypi" if self.status == "installed" else None,
            verification="verified" if self.status == "installed" else "unknown",
            path="/managed-site/tool" if self.status == "installed" else None,
            sha256="a" * 64 if self.status == "installed" else None,
            artifact_id="artifact-tool" if self.status == "installed" else None,
        )
        return CapabilityResolution(
            request.name,
            result,
            discovered=("tool",) if self.status == "installed" else (),
            attempts=2,
        )


def build_workbench(tmp_path: Path, resolution):
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=FakeGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=WorkSessionStore(runtime / "sessions.db"),
        audit_path=runtime / "audit.jsonl",
        resolution=resolution,
    )


def test_workbench_resolves_missing_capability_through_governed_runtime(tmp_path: Path):
    resolution = FakeResolution()
    wb = build_workbench(tmp_path, resolution)

    result = wb.execute(
        WorkbenchRequest(
            "workbench:capability:resolve",
            "researcher",
            "mission-1",
            reason="Need browser driver",
            arguments=(json.dumps({
                "name": "python.package:browser-driver",
                "constraints": {"source": "pypi"},
            }),),
            correlation_id="corr-resolve",
        )
    )

    assert result.status == "succeeded"
    assert result.operation == "capability_resolution"
    assert result.output["status"] == "installed"
    assert result.output["verification"] == "verified"
    assert result.output["discovered"] == ["tool"]
    assert result.receipt_id
    assert resolution.calls[0].name == "python.package:browser-driver"
    assert resolution.calls[0].actor == "researcher"
    assert resolution.calls[0].reason == "Need browser driver"
    assert resolution.calls[0].constraints == {"source": "pypi"}


def test_workbench_fails_closed_when_capability_resolution_cannot_acquire(tmp_path: Path):
    resolution = FakeResolution(status="unresolved")
    wb = build_workbench(tmp_path, resolution)

    try:
        wb.execute(
            WorkbenchRequest(
                "workbench:capability:resolve",
                "researcher",
                "mission-2",
                arguments=(json.dumps({"name": "python.package:missing"}),),
            )
        )
    except Exception as exc:
        assert "unresolved" in str(exc)
    else:
        raise AssertionError("unresolved capability must fail closed")


def test_workbench_catalog_reports_resolution_availability(tmp_path: Path):
    resolution = FakeResolution()
    wb = build_workbench(tmp_path, resolution)
    row = next(item for item in wb.census() if item.capability_id == "workbench:capability:resolve")
    assert row.available is True
    assert row.authorized is True
    assert row.healthy is True
