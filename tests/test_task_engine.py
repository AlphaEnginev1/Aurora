from pathlib import Path

import pytest

from aurora.autonomy.capability_runtime import CapabilityExecutionResult
from aurora.autonomy.models import CapabilityResult
from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import CapabilityTaskResult, TaskResult
from aurora.orchestration.task_engine import (
    DelegatedTask,
    TaskEngine,
    TaskEngineError,
    TaskStatus,
)


class FakeRouter(ModelRouter):
    def generate(self, request, capability=None):
        return InferenceResponse(
            model="fake/model",
            provider="fake",
            content=f"done:{request.metadata['task_id']}",
        )


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
                version="2.31.0",
            ),
            command=("/workspace/managed-site/bin/python", *tuple(args)),
            returncode=0,
            stdout="2.31.0\n",
            stderr="",
        )


def engine():
    from aurora.orchestration.engine import OrchestrationEngine

    return OrchestrationEngine(
        FakeRouter([ModelSpec("fake/model", "fake", "https://example.invalid")]),
        FakeCapabilityRuntime(),
    )


def test_task_engine_orders_dependencies_before_dependents(tmp_path):
    tasks = [
        DelegatedTask("research", "researcher", "collect evidence"),
        DelegatedTask(
            "inspect",
            "analyst",
            "inspect acquired data",
            capability="python.package:requests",
            args=("-c", "print('ok')"),
            depends_on=("research",),
        ),
    ]

    result = TaskEngine(engine(), tmp_path).execute(tasks)

    assert [item.task.task_id for item in result] == ["research", "inspect"]
    assert all(item.status is TaskStatus.SUCCEEDED for item in result)
    assert isinstance(result[0].model_result, TaskResult)
    assert isinstance(result[1].capability_result, CapabilityTaskResult)


def test_task_engine_blocks_failed_dependency(tmp_path):
    class FailingEngine:
        def run_task(self, **kwargs):
            raise RuntimeError("planner failure")

        def run_capability_task(self, **kwargs):
            raise AssertionError("blocked dependency must not execute")

    tasks = [
        DelegatedTask("a", "agent", "fail"),
        DelegatedTask(
            "b", "agent", "use result",
            capability="tool:rg",
            depends_on=("a",),
        ),
    ]

    result = TaskEngine(FailingEngine(), tmp_path).execute(tasks)
    assert result[0].status is TaskStatus.FAILED
    assert result[1].status is TaskStatus.BLOCKED
    assert result[1].error == "dependency did not succeed"


def test_task_engine_rejects_unknown_dependency(tmp_path):
    with pytest.raises(TaskEngineError, match="unknown task"):
        TaskEngine(engine(), tmp_path).validate(
            [DelegatedTask("a", "agent", "work", depends_on=("missing",))]
        )


def test_task_engine_rejects_cycles(tmp_path):
    with pytest.raises(TaskEngineError, match="cycle"):
        TaskEngine(engine(), tmp_path).validate(
            [
                DelegatedTask("a", "agent", "a", depends_on=("b",)),
                DelegatedTask("b", "agent", "b", depends_on=("a",)),
            ]
        )


def test_task_engine_requires_task_identity_and_objective(tmp_path):
    with pytest.raises(TaskEngineError, match="empty role or objective"):
        TaskEngine(engine(), tmp_path).validate(
            [DelegatedTask("task", "", "work")]
        )
