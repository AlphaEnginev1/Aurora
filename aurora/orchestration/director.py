"""Structured Director planning and mission execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aurora.inference.models import InferenceRequest
from aurora.inference.router import ModelRouter
from .task_engine import DelegatedTask, DelegatedTaskResult, TaskEngine


class DirectorPlanError(ValueError):
    """Raised when the Director model returns an unsafe or invalid plan."""


@dataclass(frozen=True)
class DirectorPlan:
    objective: str
    tasks: tuple[DelegatedTask, ...]
    model: str


@dataclass(frozen=True)
class MissionResult:
    objective: str
    plan: DirectorPlan
    tasks: tuple[DelegatedTaskResult, ...]


class DirectorPlanner:
    """Ask the inference fabric for a machine-valid, policy-filterable task plan."""

    _SYSTEM = """You are Aurora's Director planner.
Return ONLY a JSON object with this exact shape:
{"tasks":[{"task_id":"string","role":"string","objective":"string","capability":"string or null","args":["string"],"depends_on":["task_id"]}]}
Rules:
- Create at most 16 tasks.
- Every task_id must be unique, short, and contain no whitespace.
- role and objective must be non-empty.
- capability must be null for reasoning-only work, or a named registered Aurora capability such as tool:rg or python.package:pip.
- Never invent executable paths, shell commands, URLs, credentials, or unregistered tools.
- args are passed only to a registered executable and must be plain strings.
- Use depends_on to express required ordering.
- Keep the plan minimal and directly related to the objective.
"""

    def __init__(self, router: ModelRouter) -> None:
        self.router = router

    def plan(
        self,
        *,
        objective: str,
        context: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> DirectorPlan:
        objective = objective.strip()
        if not objective:
            raise DirectorPlanError("objective is empty")

        messages = [
            {"role": "system", "content": self._SYSTEM},
            *(context or []),
            {"role": "user", "content": objective},
        ]
        response = self.router.generate(
            InferenceRequest(
                messages=messages,
                model=model,
                temperature=0.0,
                max_tokens=3000,
                metadata={"role": "director_planner"},
            ),
            capability="reasoning",
        )
        tasks = self._parse(response.content)
        return DirectorPlan(objective=objective, tasks=tasks, model=response.model)

    @staticmethod
    def _parse(content: str) -> tuple[DelegatedTask, ...]:
        raw = content.strip()
        fence = chr(96) * 3
        if raw.startswith(fence):
            lines = raw.splitlines()
            if len(lines) < 3 or not lines[-1].strip().startswith(fence):
                raise DirectorPlanError("unterminated JSON code fence")
            raw = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DirectorPlanError("Director response is not valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
            raise DirectorPlanError("Director plan must contain a tasks array")
        if not payload["tasks"] or len(payload["tasks"]) > 16:
            raise DirectorPlanError("Director plan must contain 1 to 16 tasks")

        tasks: list[DelegatedTask] = []
        for item in payload["tasks"]:
            if not isinstance(item, dict):
                raise DirectorPlanError("each task must be an object")
            try:
                task_id = item["task_id"]
                role = item["role"]
                task_objective = item["objective"]
            except KeyError as exc:
                raise DirectorPlanError(f"task missing required field: {exc.args[0]}") from exc
            capability = item.get("capability")
            args = item.get("args", [])
            depends_on = item.get("depends_on", [])
            if not isinstance(task_id, str) or not isinstance(role, str) or not isinstance(task_objective, str):
                raise DirectorPlanError("task identity, role, and objective must be strings")
            if capability is not None and not isinstance(capability, str):
                raise DirectorPlanError("task capability must be a string or null")
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise DirectorPlanError("task args must be a string array")
            if not isinstance(depends_on, list) or not all(isinstance(dep, str) for dep in depends_on):
                raise DirectorPlanError("task depends_on must be a string array")
            tasks.append(
                DelegatedTask(
                    task_id=task_id,
                    role=role,
                    objective=task_objective,
                    capability=capability,
                    args=tuple(args),
                    depends_on=tuple(depends_on),
                )
            )
        return tuple(tasks)


class DirectorService:
    """Turn a Director objective into a validated plan and delegated execution."""

    def __init__(self, planner: DirectorPlanner, task_engine: TaskEngine) -> None:
        self.planner = planner
        self.task_engine = task_engine

    def run_mission(
        self,
        *,
        objective: str,
        context: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> MissionResult:
        plan = self.planner.plan(
            objective=objective,
            context=context,
            model=model,
        )
        results = self.task_engine.execute(plan.tasks)
        return MissionResult(
            objective=plan.objective,
            plan=plan,
            tasks=results,
        )


__all__ = [
    "DirectorPlan",
    "DirectorPlanError",
    "DirectorPlanner",
    "DirectorService",
    "MissionResult",
]
