"""Provider-neutral orchestration engine."""

from dataclasses import dataclass
from typing import Any

from aurora.inference.models import InferenceRequest, InferenceResponse
from aurora.inference.router import ModelRouter

@dataclass(frozen=True)
class TaskResult:
    task_id: str
    role: str
    response: InferenceResponse

class OrchestrationEngine:
    """Route an agent task through Aurora model routing."""
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    def run_task(self, *, task_id: str, role: str, objective: str,
                 capability: str = "reasoning", model: str | None = None,
                 context: list[dict[str, Any]] | None = None) -> TaskResult:
        messages = list(context or [])
        messages.append({"role": "user", "content": objective})
        request = InferenceRequest(messages=messages, model=model,
                                   metadata={"task_id": task_id, "role": role})
        response = self._router.generate(request, capability=capability)
        return TaskResult(task_id=task_id, role=role, response=response)
