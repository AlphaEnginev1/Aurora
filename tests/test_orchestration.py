from pathlib import Path

from aurora.autonomy.acquisition import AcquisitionResult
from aurora.autonomy.capability_runtime import CapabilityExecutionResult
from aurora.autonomy.models import CapabilityRequest, CapabilityResult
from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import OrchestrationEngine


class FakeRouter(ModelRouter):
    def generate(self, request, capability=None):
        assert capability == "reasoning"
        assert request.metadata["task_id"] == "task-1"
        assert request.metadata["role"] == "researcher"
        return InferenceResponse(model="fake/model", provider="fake", content="verified")


class FakeCapabilityRuntime:
    def __init__(self):
        self.calls = []

    def execute(self, request, *, workspace, args=()):
        self.calls.append((request, Path(workspace), tuple(args)))
        return CapabilityExecutionResult(
            capability=CapabilityResult(
                name=request.name,
                status="installed",
                source="pypi",
                verification="verified",
                path=str(workspace / "managed-site"),
                version="1.0",
            ),
            command=("/workspace/managed-site/bin/tool", *tuple(args)),
            returncode=0,
            stdout="ok\n",
            stderr="",
        )


def test_orchestration_routes_agent_task():
    engine = OrchestrationEngine(
        FakeRouter([ModelSpec("fake/model", "fake", "https://example.invalid")])
    )
    result = engine.run_task(
        task_id="task-1",
        role="researcher",
        objective="Investigate the target.",
    )
    assert result.task_id == "task-1"
    assert result.role == "researcher"
    assert result.response.content == "verified"


def test_orchestration_routes_capability_task():
    capability_runtime = FakeCapabilityRuntime()
    engine = OrchestrationEngine(
        FakeRouter([ModelSpec("fake/model", "fake", "https://example.invalid")]),
        capability_runtime,
    )
    request = CapabilityRequest(
        "python.package:requests",
        "researcher",
        reason="perform source retrieval",
    )

    result = engine.run_capability_task(
        task_id="task-2",
        role="researcher",
        request=request,
        workspace=Path("/tmp/task-2"),
        args=("-c", "print('ok')"),
    )

    assert result.task_id == "task-2"
    assert result.role == "researcher"
    assert result.execution.returncode == 0
    assert result.execution.capability.verification == "verified"
    assert capability_runtime.calls[0][0] == request
    assert capability_runtime.calls[0][1] == Path("/tmp/task-2")
    assert capability_runtime.calls[0][2] == ("-c", "print('ok')")


def test_orchestration_refuses_capability_task_without_runtime():
    engine = OrchestrationEngine(
        FakeRouter([ModelSpec("fake/model", "fake", "https://example.invalid")])
    )
    request = CapabilityRequest("tool:rg", "researcher")
    try:
        engine.run_capability_task(
            task_id="task-3",
            role="researcher",
            request=request,
            workspace="/tmp/task-3",
        )
    except RuntimeError as exc:
        assert "runtime is not configured" in str(exc)
    else:
        raise AssertionError("expected missing capability runtime failure")
