"""Deterministic delegated task engine.

The Director creates structured tasks; this engine validates dependencies and
dispatches each task through the existing orchestration and capability boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Sequence

from aurora.autonomy.capability_runtime import CapabilityExecutionError
from aurora.autonomy.models import CapabilityRequest
from .engine import OrchestrationEngine, TaskResult


class TaskEngineError(RuntimeError):
    """Raised when a delegated task graph is invalid or cannot execute safely."""


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DelegatedTask:
    task_id: str
    role: str
    objective: str
    capability: str | None = None
    args: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class DelegatedTaskResult:
    task: DelegatedTask
    status: TaskStatus
    model_result: TaskResult | None = None
    capability_result: object | None = None
    error: str | None = None


@dataclass
class TaskEngine:
    """Execute a validated finite task graph through OrchestrationEngine."""
    orchestration: OrchestrationEngine
    workspace_root: Path
    _results: dict[str, DelegatedTaskResult] = field(default_factory=dict, init=False)

    def validate(self, tasks: Sequence[DelegatedTask]) -> tuple[DelegatedTask, ...]:
        normalized = tuple(tasks)
        if not normalized:
            raise TaskEngineError("task plan is empty")
        ids = [task.task_id for task in normalized]
        if any(not task_id or any(ch.isspace() for ch in task_id) for task_id in ids):
            raise TaskEngineError("task ids must be non-empty and contain no whitespace")
        if len(set(ids)) != len(ids):
            raise TaskEngineError("task ids must be unique")
        known = set(ids)
        for task in normalized:
            if not task.role.strip() or not task.objective.strip():
                raise TaskEngineError(f"task {task.task_id} has an empty role or objective")
            if any("\x00" in arg for arg in task.args):
                raise TaskEngineError(f"task {task.task_id} contains a null-byte argument")
            if any(dep not in known for dep in task.depends_on):
                missing = sorted(set(task.depends_on) - known)
                raise TaskEngineError(
                    f"task {task.task_id} depends on unknown task(s): {', '.join(missing)}"
                )
            if task.task_id in task.depends_on:
                raise TaskEngineError(f"task {task.task_id} cannot depend on itself")
        self._assert_acyclic(normalized)
        return normalized

    def execute(self, tasks: Sequence[DelegatedTask]) -> tuple[DelegatedTaskResult, ...]:
        plan = self.validate(tasks)
        self._results.clear()
        remaining = {task.task_id: task for task in plan}
        ordered: list[DelegatedTaskResult] = []

        while remaining:
            progressed = False
            for task_id, task in tuple(remaining.items()):
                if any(dep not in self._results for dep in task.depends_on):
                    continue
                dependencies = [self._results[dep] for dep in task.depends_on]
                if any(result.status is not TaskStatus.SUCCEEDED for result in dependencies):
                    result = DelegatedTaskResult(
                        task,
                        TaskStatus.BLOCKED,
                        error="dependency did not succeed",
                    )
                    self._results[task_id] = result
                    ordered.append(result)
                    del remaining[task_id]
                    progressed = True
                    continue

                self._results[task_id] = DelegatedTaskResult(task, TaskStatus.RUNNING)
                try:
                    result = self._execute_task(task)
                except (CapabilityExecutionError, RuntimeError, ValueError) as exc:
                    result = DelegatedTaskResult(
                        task,
                        TaskStatus.FAILED,
                        error=str(exc),
                    )
                else:
                    result = DelegatedTaskResult(
                        task,
                        TaskStatus.SUCCEEDED,
                        model_result=result if isinstance(result, TaskResult) else None,
                        capability_result=None if isinstance(result, TaskResult) else result,
                    )
                self._results[task_id] = result
                ordered.append(result)
                del remaining[task_id]
                progressed = True

            if not progressed:
                raise TaskEngineError("task graph made no progress; refusing deadlock")
        return tuple(ordered)

    def _execute_task(self, task: DelegatedTask):
        if task.capability:
            request = CapabilityRequest(
                name=task.capability,
                actor=task.role,
                reason=task.objective,
            )
            # Managed capabilities are activated in the shared runtime root so
            # stable acquisition paths (for example /workspace/managed-site)
            # remain valid. Task-specific data must live in the task workspace.
            workspace = self.workspace_root
            return self.orchestration.run_capability_task(
                task_id=task.task_id,
                role=task.role,
                request=request,
                workspace=workspace,
                args=task.args,
            )
        return self.orchestration.run_task(
            task_id=task.task_id,
            role=task.role,
            objective=task.objective,
        )

    @staticmethod
    def _assert_acyclic(tasks: Sequence[DelegatedTask]) -> None:
        graph = {task.task_id: set(task.depends_on) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise TaskEngineError("task graph contains a dependency cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dep in graph[task_id]:
                visit(dep)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)


__all__ = [
    "DelegatedTask",
    "DelegatedTaskResult",
    "TaskEngine",
    "TaskEngineError",
    "TaskStatus",
]
