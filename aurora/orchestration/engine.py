"""Provider-neutral orchestration engine."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from aurora.autonomy.capability_runtime import (
    CapabilityExecutionError,
    CapabilityExecutionResult,
    CapabilityRuntime,
)
from aurora.autonomy.models import CapabilityRequest, CapabilityResult
from aurora.workbench import UniversalWorkbench, WorkbenchRequest
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
        workbench: UniversalWorkbench | None = None,
    ) -> None:
        self._router = router
        self._capability_runtime = capability_runtime
        self._workbench = workbench

    def prepare_capabilities(
        self,
        *,
        names: Sequence[str],
        actor: str,
        reason: str,
    ) -> tuple[CapabilityResult, ...]:
        if not names:
            return ()
        if self._capability_runtime is None:
            return ()
        prepare = getattr(self._capability_runtime, "prepare_capabilities", None)
        if prepare is None:
            raise CapabilityExecutionError(
                "capability runtime does not support verified artifact preparation"
            )
        return prepare(names, actor=actor, reason=reason)

    def run_task(
        self,
        *,
        task_id: str,
        role: str,
        objective: str,
        capability: str = "reasoning",
        model: str | None = None,
        model_preferences: Sequence[str] | None = None,
        project_id: str | None = None,
        context: list[dict[str, Any]] | None = None,
        skill_ids: Sequence[str] | None = None,
        skill_tags: Sequence[str] | None = None,
        task_type: str | None = None,
        learned_model_scores: Mapping[str, float] | None = None,
    ) -> TaskResult:
        messages = list(context or [])
        messages.append({"role": "user", "content": objective})
        request_metadata: dict[str, Any] = {
            "task_id": task_id,
            "role": role,
        }
        if project_id:
            request_metadata["project_id"] = project_id
        if skill_ids:
            request_metadata["skill_ids"] = list(skill_ids)
        if skill_tags:
            request_metadata["skill_tags"] = list(skill_tags)
        if task_type:
            request_metadata["task_type"] = task_type
        if learned_model_scores:
            request_metadata["learned_model_scores"] = dict(learned_model_scores)
        request_metadata["context_tokens"] = max(
            1,
            sum(len(str(message.get("content", ""))) for message in messages) // 4,
        )
        request = InferenceRequest(
            messages=messages,
            model=model,
            metadata=request_metadata,
        )
        if model_preferences:
            response = self._router.generate(
                request,
                capability=capability,
                preferred_models=model_preferences,
            )
        else:
            response = self._router.generate(
                request,
                capability=capability,
            )
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
        """Execute an agent-requested capability through the governed Workbench or broker."""
        if request.name.startswith("workbench:"):
            if self._workbench is None:
                raise CapabilityExecutionError("Universal Workbench is not configured")
            result = self._workbench.execute(
                WorkbenchRequest(
                    capability=request.name,
                    actor=request.actor,
                    mission_id=request.constraints.get("mission_id", task_id),
                    reason=request.reason,
                    arguments=tuple(args),
                    options=dict(request.constraints),
                )
            )
            return CapabilityTaskResult(
                task_id=task_id,
                role=role,
                execution=result,
            )
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
