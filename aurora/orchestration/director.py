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
class DirectorReview:
    decision: str
    rationale: str
    tasks: tuple[DelegatedTask, ...]
    model: str


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

    _REVIEW_SYSTEM = """You are Aurora's Director reviewer.
Review the current mission state and decide whether the objective is complete.
Return ONLY a JSON object:
{"decision":"complete|continue|blocked","rationale":"string","tasks":[{"task_id":"string","role":"string","objective":"string","capability":"string or null","args":["string"],"depends_on":["task_id"]}]}
Rules:
- Choose complete only when the evidence in context is sufficient to satisfy the objective.
- Choose blocked when safe progress is impossible under the current policies or available capabilities.
- Choose continue when additional work is needed; provide the minimal next task graph.
- Create at most 16 follow-up tasks.
- Never invent executable paths, shell commands, URLs, credentials, or unregistered tools.
"""
    
    def review(
        self,
        *,
        objective: str,
        context: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> DirectorReview:
        objective = objective.strip()
        if not objective:
            raise DirectorPlanError("objective is empty")
        messages = [
            {"role": "system", "content": self._REVIEW_SYSTEM},
            *(context or []),
            {"role": "user", "content": objective},
        ]
        response = self.router.generate(
            InferenceRequest(
                messages=messages,
                model=model,
                temperature=0.0,
                max_tokens=3000,
                metadata={"role": "director_reviewer"},
            ),
            capability="reasoning",
        )
        decision, rationale, tasks = self._parse_review(response.content)
        return DirectorReview(
            decision=decision,
            rationale=rationale,
            tasks=tasks,
            model=response.model,
        )

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


    @classmethod
    def _parse_review(cls, content: str) -> tuple[str, str, tuple[DelegatedTask, ...]]:
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
            raise DirectorPlanError("Director review is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise DirectorPlanError("Director review must be an object")
        decision = payload.get("decision")
        rationale = payload.get("rationale", "")
        tasks_payload = payload.get("tasks", [])
        if decision not in {"complete", "continue", "blocked"}:
            raise DirectorPlanError("Director review decision is invalid")
        if not isinstance(rationale, str) or not rationale.strip():
            raise DirectorPlanError("Director review rationale must be non-empty")
        if not isinstance(tasks_payload, list):
            raise DirectorPlanError("Director review tasks must be an array")
        if decision == "complete" and tasks_payload:
            raise DirectorPlanError("complete review cannot contain tasks")
        if decision in {"continue", "blocked"} and decision == "blocked" and tasks_payload:
            raise DirectorPlanError("blocked review cannot contain tasks")
        if decision == "continue" and not tasks_payload:
            raise DirectorPlanError("continue review must contain follow-up tasks")
        if len(tasks_payload) > 16:
            raise DirectorPlanError("Director review cannot contain more than 16 tasks")

        tasks: list[DelegatedTask] = []
        for item in tasks_payload:
            if not isinstance(item, dict):
                raise DirectorPlanError("each review task must be an object")
            try:
                task_id = item["task_id"]
                role = item["role"]
                task_objective = item["objective"]
            except KeyError as exc:
                raise DirectorPlanError(
                    f"review task missing required field: {exc.args[0]}"
                ) from exc
            capability = item.get("capability")
            args = item.get("args", [])
            depends_on = item.get("depends_on", [])
            if not isinstance(task_id, str) or not isinstance(role, str) or not isinstance(task_objective, str):
                raise DirectorPlanError("review task identity, role, and objective must be strings")
            if capability is not None and not isinstance(capability, str):
                raise DirectorPlanError("review task capability must be a string or null")
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise DirectorPlanError("review task args must be a string array")
            if not isinstance(depends_on, list) or not all(isinstance(dep, str) for dep in depends_on):
                raise DirectorPlanError("review task depends_on must be a string array")
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
        return decision, rationale.strip(), tuple(tasks)


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
    "DirectorReview",
    "DirectorPlanner",
    "DirectorService",
    "MissionResult",
]
