"""Provider-neutral orchestration engine."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from aurora.autonomy.capability_runtime import (
    CapabilityExecutionError,
    CapabilityExecutionResult,
    CapabilityRuntime,
)
from aurora.autonomy.models import CapabilityRequest
from aurora.inference.models import InferenceRequest, InferenceResponse
from aurora.inference.router import ModelRouter


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    role: str
    response: InferenceResponse


@dataclass(frozen=True)
class CapabilityTaskResult:
    task_id: str
    role: str
    execution: CapabilityExecutionResult


class OrchestrationEngine:
    """Route model tasks and brokered capability tasks."""

    def __init__(
        self,
        router: ModelRouter,
        capability_runtime: CapabilityRuntime | None = None,
    ) -> None:
        self._router = router
        self._capability_runtime = capability_runtime

    def run_task(
        self,
        *,
        task_id: str,
        role: str,
        objective: str,
        capability: str = "reasoning",
        model: str | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> TaskResult:
        messages = list(context or [])
        messages.append({"role": "user", "content": objective})
        request = InferenceRequest(
            messages=messages,
            model=model,
            metadata={"task_id": task_id, "role": role},
        )
        response = self._router.generate(request, capability=capability)
        return TaskResult(task_id=task_id, role=role, response=response)

    def run_capability_task(
        self,
        *,
        task_id: str,
        role: str,
        request: CapabilityRequest,
        workspace: str | Path,
        args: Sequence[str] = (),
    ) -> CapabilityTaskResult:
        """Execute an agent-requested capability through the brokered runtime."""
        if self._capability_runtime is None:
            raise CapabilityExecutionError(
                "orchestration capability runtime is not configured"
            )
        execution = self._capability_runtime.execute(
            request,
            workspace=workspace,
            args=args,
        )
        return CapabilityTaskResult(
            task_id=task_id,
            role=role,
            execution=execution,
        )
