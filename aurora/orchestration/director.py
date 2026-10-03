"""Structured Director planning and mission execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from aurora.inference.router import ModelRouter
from .director_provider import (
    DirectorProvider,
    DirectorToolCall,
    DirectorToolSpec,
    InferenceDirectorProvider,
)
from aurora.organization import OrganizationConfigError, OrganizationRegistry
from aurora.projects import ProjectRegistry
from aurora.skills import SkillRegistry, SkillRegistryError
from .task_engine import DelegatedTask, DelegatedTaskResult, TaskEngine


class DirectorPlanError(ValueError):
    """Raised when the Director model returns an unsafe or invalid plan."""


@dataclass(frozen=True)
class DirectorReview:
    decision: str
    rationale: str
    tasks: tuple[DelegatedTask, ...]
    model: str
    considered_alternatives: tuple[str, ...] = ()


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

    _DELEGATE_TOOL = DirectorToolSpec(
        name="delegate_task",
        description="Request Aurora to validate and enqueue one delegated task. The tool never executes the task itself.",
        parameters={
            "type": "object",
            "required": ["task_id", "role", "objective"],
            "properties": {
                "task_id": {"type": "string"},
                "role": {"type": "string"},
                "objective": {"type": "string"},
                "capability": {"type": ["string", "null"]},
                "args": {"type": "array", "items": {"type": "string"}},
                "depends_on": {"type": "array", "items": {"type": "string"}},
                "skills": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    )

    _SYSTEM = """You are Aurora's Director planner.
Return ONLY a JSON object with this exact shape:
{"tasks":[{"task_id":"string","role":"string","objective":"string","capability":"string or null","args":["string"],"depends_on":["task_id"],"skills":["skill-id"]}]}
Rules:
- Create at most 16 tasks.
- Every task_id must be unique, short, and contain no whitespace.
- role and objective must be non-empty.
- capability must be null for reasoning-only work, or a named registered Aurora capability such as tool:rg or python.package:pip.
- Never invent executable paths, shell commands, URLs, credentials, or unregistered tools.
- args are passed only to a registered executable and must be plain strings.
- Use depends_on to express required ordering.
- skills may contain registered specialist skills when the task benefits from specialist procedures.
- Never invent skills; use only registered skills presented in the prompt.
- Keep the plan minimal and directly related to the objective.
"""

    def __init__(
        self,
        router: ModelRouter,
        organization: OrganizationRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        project_registry: ProjectRegistry | None = None,
        provider: DirectorProvider | None = None,
    ) -> None:
        self.router = router
        self.provider = provider or InferenceDirectorProvider(router)
        self.organization = organization
        self.skill_registry = skill_registry
        self.project_registry = project_registry

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

        system = self._SYSTEM
        if self.organization is not None:
            roles = ", ".join(sorted(self.organization.agents_by_role()))
            system += f"\nRegistered roles: {roles}\n"
        if self.skill_registry is not None:
            system += "\nRegistered specialist skill catalog:\n"
            system += self.skill_registry.catalog() + "\n"
        if self.project_registry is not None:
            system += "\nRegistered Aurora project catalog:\n"
            system += self.project_registry.catalog() + "\n"
        messages = [
            {"role": "system", "content": system},
            *(context or []),
            {"role": "user", "content": objective},
        ]
        response = self.provider.generate(
            messages=messages,
            model=model,
            role="director_planner",
            tools=(self._DELEGATE_TOOL,),
        )
        tasks = (
            self._parse_tool_calls(response.tool_calls)
            if response.tool_calls
            else self._parse(response.content)
        )
        tasks = self._normalize_organization_roles(tasks)
        self._validate_organization(tasks)
        return DirectorPlan(objective=objective, tasks=tasks, model=response.model)

    _REVIEW_SYSTEM = """You are Aurora's Director reviewer.
Review the current mission state and decide whether the objective is complete.
Return ONLY a JSON object:
{"decision":"complete|continue|blocked","rationale":"string","considered_alternatives":["string"],"tasks":[{"task_id":"string","role":"string","objective":"string","capability":"string or null","args":["string"],"depends_on":["task_id"],"skills":["skill-id"]}]}
Rules:
- Choose complete only when the evidence in context is sufficient to satisfy the objective.
- Choose blocked when safe progress is impossible under the current policies or available capabilities.
- Choose continue when additional work is needed; provide the minimal next task graph.
- considered_alternatives lists only materially plausible alternatives that were explicitly considered for this decision; an empty list is valid.
- Never claim an alternative was tested unless the context contains evidence that it was executed or observed.
- Create at most 16 follow-up tasks.
- Never invent executable paths, shell commands, URLs, credentials, unregistered tools, or skills.
- Use only registered skills presented in the prompt.
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
        system = self._REVIEW_SYSTEM
        if self.organization is not None:
            roles = ", ".join(sorted(self.organization.agents_by_role()))
            system += f"\nRegistered roles: {roles}\n"
        if self.skill_registry is not None:
            system += "\nRegistered specialist skill catalog:\n"
            system += self.skill_registry.catalog() + "\n"
        if self.project_registry is not None:
            system += "\nRegistered Aurora project catalog:\n"
            system += self.project_registry.catalog() + "\n"
        messages = [
            {"role": "system", "content": system},
            *(context or []),
            {"role": "user", "content": objective},
        ]
        response = self.provider.generate(
            messages=messages,
            model=model,
            role="director_reviewer",
            tools=(self._DELEGATE_TOOL,),
        )
        if response.tool_calls:
            decision, rationale, tasks, considered_alternatives = self._parse_review_tool_calls(response.tool_calls)
        else:
            decision, rationale, tasks, considered_alternatives = self._parse_review(response.content)
        tasks = self._normalize_organization_roles(tasks)
        self._validate_organization(tasks)
        return DirectorReview(
            decision=decision,
            rationale=rationale,
            tasks=tasks,
            model=response.model,
            considered_alternatives=considered_alternatives,
        )

    @classmethod
    def _parse_tool_calls(cls, calls: tuple[DirectorToolCall, ...]) -> tuple[DelegatedTask, ...]:
        """Convert provider proposals into tasks; no tool is executed here."""
        if not calls or len(calls) > 16:
            raise DirectorPlanError("Director tool-call plan must contain 1 to 16 calls")
        tasks = []
        for call in calls:
            if call.name != cls._DELEGATE_TOOL.name:
                raise DirectorPlanError(f"Director requested unregistered tool: {call.name}")
            tasks.append(cls._task_from_tool_call(call))
        return tuple(tasks)

    @classmethod
    def _parse_review_tool_calls(
        cls, calls: tuple[DirectorToolCall, ...],
    ) -> tuple[str, str, tuple[DelegatedTask, ...], tuple[str, ...]]:
        tasks = cls._parse_tool_calls(calls)
        return "continue", "Director requested additional delegated work.", tasks, ()

    @staticmethod
    def _task_from_tool_call(call: DirectorToolCall) -> DelegatedTask:
        args = call.arguments
        if not isinstance(args, dict):
            raise DirectorPlanError("Director tool arguments must be an object")
        required = ("task_id", "role", "objective")
        if any(key not in args for key in required):
            missing = next(key for key in required if key not in args)
            raise DirectorPlanError(f"delegate_task missing required argument: {missing}")
        task_id, role, objective = (args[key] for key in required)
        capability = args.get("capability")
        task_args = args.get("args", [])
        depends_on = args.get("depends_on", [])
        skills = args.get("skills", [])
        if not all(isinstance(value, str) for value in (task_id, role, objective)):
            raise DirectorPlanError("delegate_task identity, role, and objective must be strings")
        if capability is not None and not isinstance(capability, str):
            raise DirectorPlanError("delegate_task capability must be a string or null")
        if not isinstance(task_args, list) or not all(isinstance(value, str) for value in task_args):
            raise DirectorPlanError("delegate_task args must be a string array")
        if not isinstance(depends_on, list) or not all(isinstance(value, str) for value in depends_on):
            raise DirectorPlanError("delegate_task depends_on must be a string array")
        if not isinstance(skills, list) or not all(isinstance(value, str) and value.strip() for value in skills):
            raise DirectorPlanError("delegate_task skills must be a string array")
        if not task_id.strip() or any(char.isspace() for char in task_id):
            raise DirectorPlanError("delegate_task task_id must be short and contain no whitespace")
        if not role.strip() or not objective.strip():
            raise DirectorPlanError("delegate_task role and objective must be non-empty")
        return DelegatedTask(
            task_id=task_id,
            role=role,
            objective=objective,
            capability=capability,
            args=tuple(task_args),
            depends_on=tuple(depends_on),
            skills=tuple(skills),
        )

    def _normalize_organization_roles(
        self, tasks: tuple[DelegatedTask, ...]
    ) -> tuple[DelegatedTask, ...]:
        """Canonicalize harmless role-name casing before organization validation.

        Model output is treated as untrusted structured data. Case-insensitive
        matching is allowed only when it resolves to exactly one registered
        Aurora role/agent identity; the canonical registry role is then stored
        in the task so downstream validation remains exact and fail-closed.
        """
        if self.organization is None:
            return tasks

        by_casefold: dict[str, set[str]] = {}
        for agent in self.organization.agents.values():
            for identity in (agent.role, agent.agent_id):
                by_casefold.setdefault(identity.strip().casefold(), set()).add(agent.role)

        normalized: list[DelegatedTask] = []
        for task in tasks:
            role = task.role.strip()
            try:
                self.organization.resolve_agent(role)
                canonical = role
            except OrganizationConfigError:
                matches = by_casefold.get(role.casefold(), set())
                if len(matches) != 1:
                    raise
                canonical = next(iter(matches))
            normalized.append(replace(task, role=canonical) if canonical != task.role else task)
        return tuple(normalized)

    def _validate_organization(self, tasks: tuple[DelegatedTask, ...]) -> None:
        if self.organization is None:
            return
        try:
            for task in tasks:
                self.organization.validate_task(
                    role=task.role,
                    capability=task.capability,
                )
        except OrganizationConfigError as exc:
            raise DirectorPlanError(str(exc)) from exc
        if self.skill_registry is not None and self.organization is not None:
            try:
                for task in tasks:
                    agent = self.organization.resolve_agent(task.role)
                    self.skill_registry.for_agent(
                        role=agent.role,
                        agent_capabilities=agent.capabilities,
                        default_skill_ids=agent.skill_ids,
                        requested_skill_ids=task.skills,
                    )
            except (OrganizationConfigError, SkillRegistryError) as exc:
                raise DirectorPlanError(str(exc)) from exc

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
            skills = item.get("skills", [])
            if not isinstance(task_id, str) or not isinstance(role, str) or not isinstance(task_objective, str):
                raise DirectorPlanError("task identity, role, and objective must be strings")
            if capability is not None and not isinstance(capability, str):
                raise DirectorPlanError("task capability must be a string or null")
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise DirectorPlanError("task args must be a string array")
            if not isinstance(depends_on, list) or not all(isinstance(dep, str) for dep in depends_on):
                raise DirectorPlanError("task depends_on must be a string array")
            if not isinstance(skills, list) or not all(isinstance(skill, str) and skill.strip() for skill in skills):
                raise DirectorPlanError("task skills must be a string array")
            tasks.append(
                DelegatedTask(
                    task_id=task_id,
                    role=role,
                    objective=task_objective,
                    capability=capability,
                    args=tuple(args),
                    depends_on=tuple(depends_on),
                    skills=tuple(skills),
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
        alternatives_payload = payload.get("considered_alternatives", [])
        if not isinstance(alternatives_payload, list) or not all(
            isinstance(item, str) and item.strip() for item in alternatives_payload
        ):
            raise DirectorPlanError("Director review considered_alternatives must be a string array")
        if len(alternatives_payload) > 8:
            raise DirectorPlanError("Director review cannot contain more than 8 alternatives")
        considered_alternatives = tuple(dict.fromkeys(item.strip() for item in alternatives_payload))
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
            skills = item.get("skills", [])
            if not isinstance(task_id, str) or not isinstance(role, str) or not isinstance(task_objective, str):
                raise DirectorPlanError("review task identity, role, and objective must be strings")
            if capability is not None and not isinstance(capability, str):
                raise DirectorPlanError("review task capability must be a string or null")
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise DirectorPlanError("review task args must be a string array")
            if not isinstance(depends_on, list) or not all(isinstance(dep, str) for dep in depends_on):
                raise DirectorPlanError("review task depends_on must be a string array")
            if not isinstance(skills, list) or not all(isinstance(skill, str) and skill.strip() for skill in skills):
                raise DirectorPlanError("review task skills must be a string array")
            tasks.append(
                DelegatedTask(
                    task_id=task_id,
                    role=role,
                    objective=task_objective,
                    capability=capability,
                    args=tuple(args),
                    depends_on=tuple(depends_on),
                    skills=tuple(skills),
                )
            )
        return decision, rationale.strip(), tuple(tasks), considered_alternatives


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
