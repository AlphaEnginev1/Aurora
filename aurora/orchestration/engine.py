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
from .agent_workbench import AgentWorkbenchToolError, AgentWorkbenchToolLoop
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
        self._workbench_loop = (
            AgentWorkbenchToolLoop(workbench) if workbench is not None else None
        )

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
        mission_id: str | None = None,
        context: list[dict[str, Any]] | None = None,
        skill_ids: Sequence[str] | None = None,
        skill_tags: Sequence[str] | None = None,
        task_type: str | None = None,
        learned_model_scores: Mapping[str, float] | None = None,
        use_workbench: bool = True,
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
        try:
            if use_workbench and self._workbench_loop is not None:
                response = self._workbench_loop.run(
                    request=request,
                    actor=role,
                    mission_id=mission_id or task_id,
                    reason=objective,
                    preferred_models=tuple(model_preferences or ()),
                    router=self._router,
                    capability=capability,
                    task_id=task_id,
                )
            elif model_preferences:
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
        except AgentWorkbenchToolError as exc:
            raise CapabilityExecutionError(str(exc)) from exc
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
                    idempotency_key=(
                        str(request.constraints["idempotency_key"])
                        if request.constraints.get("idempotency_key")
                        else None
                    ),
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
