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
from aurora.organization import OrganizationConfigError, OrganizationRegistry
from aurora.skills import SkillRegistry, SkillRegistryError


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
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class DelegatedTaskResult:
    task: DelegatedTask
    status: TaskStatus
    model_result: TaskResult | None = None
    capability_result: object | None = None
    error: str | None = None


@dataclass
class TaskEngine:
    """Execute delegated tasks through OrchestrationEngine."""

    orchestration: OrchestrationEngine
    workspace_root: Path
    organization: OrganizationRegistry | None = None
    skill_registry: SkillRegistry | None = None
    skill_learning: object | None = None
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
            self._validate_task_fields(task)
            if self.organization is not None:
                try:
                    agent = self.organization.validate_task(
                        role=task.role,
                        capability=task.capability,
                    )
                    if self.skill_registry is not None:
                        self.skill_registry.for_agent(
                            role=agent.role,
                            agent_capabilities=agent.capabilities,
                            default_skill_ids=agent.skill_ids,
                            requested_skill_ids=task.skills,
                        )
                except (OrganizationConfigError, SkillRegistryError) as exc:
                    raise TaskEngineError(str(exc)) from exc
            elif task.skills:
                raise TaskEngineError("skill assignments require an organization registry")
            if any(dep not in known for dep in task.depends_on):
                missing = sorted(set(task.depends_on) - known)
                raise TaskEngineError(
                    f"task {task.task_id} depends on unknown task(s): {', '.join(missing)}"
                )
            if task.task_id in task.depends_on:
                raise TaskEngineError(f"task {task.task_id} cannot depend on itself")
        self._assert_acyclic(normalized)
        return normalized

    @staticmethod
    def _validate_task_fields(task: DelegatedTask) -> None:
        if not task.task_id or any(ch.isspace() for ch in task.task_id):
            raise TaskEngineError(
                f"task {task.task_id!r} has an invalid task id"
            )
        if not task.role.strip() or not task.objective.strip():
            raise TaskEngineError(f"task {task.task_id} has an empty role or objective")
        if any("\x00" in arg for arg in task.args):
            raise TaskEngineError(f"task {task.task_id} contains a null-byte argument")

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

                result = self.execute_one(task)
                self._results[task_id] = result
                ordered.append(result)
                del remaining[task_id]
                progressed = True

            if not progressed:
                raise TaskEngineError("task graph made no progress; refusing deadlock")
        return tuple(ordered)

    def execute_one(self, task: DelegatedTask) -> DelegatedTaskResult:
        """Execute one already-dependency-resolved task for a durable worker."""
        self._validate_task_fields(task)
        try:
            result = self._execute_task(task)
        except (CapabilityExecutionError, RuntimeError, ValueError) as exc:
            return DelegatedTaskResult(
                task,
                TaskStatus.FAILED,
                error=str(exc),
            )
        return DelegatedTaskResult(
            task,
            TaskStatus.SUCCEEDED,
            model_result=result if isinstance(result, TaskResult) else None,
            capability_result=None if isinstance(result, TaskResult) else result,
        )

    def skill_assignment_for(self, task: DelegatedTask):
        if self.skill_registry is None or self.organization is None:
            return None
        agent = self.organization.resolve_agent(task.role)
        return self.skill_registry.for_agent(
            role=agent.role,
            agent_capabilities=agent.capabilities,
            default_skill_ids=agent.skill_ids,
            requested_skill_ids=task.skills,
        )

    @staticmethod
    def _classify_task(objective: str, tags: Sequence[str]) -> str:
        text = objective.lower()
        tagset = {tag.lower() for tag in tags}
        rules = (
            ("security", {"security", "audit", "threat", "vulnerability"}),
            ("research", {"research", "literature", "paper", "evidence", "study"}),
            ("data", {"data", "dataset", "pipeline", "analysis", "statistics"}),
            ("verification", {"verify", "validation", "test", "reproduce", "check"}),
            ("operations", {"runtime", "recovery", "incident", "health", "monitor"}),
            ("discovery", {"discover", "novel", "hypothesis", "explore", "prior art"}),
            ("synthesis", {"synthesize", "summary", "decision", "brief", "integrate"}),
            ("engineering", {"code", "implement", "debug", "refactor", "software"}),
        )
        for name, keywords in rules:
            if name in tagset or any(keyword in text for keyword in keywords):
                return name
        return "general"

    def _execute_task(self, task: DelegatedTask):
        skill_assignment = None
        if self.skill_registry is not None and self.organization is not None:
            agent = self.organization.resolve_agent(task.role)
            skill_assignment = self.skill_registry.for_agent(
                role=agent.role,
                agent_capabilities=agent.capabilities,
                default_skill_ids=agent.skill_ids,
                requested_skill_ids=task.skills,
            )
        skill_preferences = skill_assignment.model_preferences if skill_assignment else ()
        skill_tags = skill_assignment.tags if skill_assignment else ()
        skill_ids = skill_assignment.skill_ids if skill_assignment else ()
        learned_model_scores = (
            self.skill_learning.model_scores_for(skill_ids)
            if self.skill_learning is not None and skill_ids
            else {}
        )
        task_type = self._classify_task(task.objective, skill_tags)
        organization_preferences = (
            self.organization.model_preferences_for(task.role)
            if self.organization is not None
            else ()
        )
        model_preferences = tuple(dict.fromkeys((*skill_preferences, *organization_preferences)))
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
        skill_context = (
            skill_assignment.render_system_context()
            if skill_assignment is not None
            else ""
        )
        return self.orchestration.run_task(
            task_id=task.task_id,
            role=task.role,
            objective=task.objective,
            model_preferences=model_preferences,
            skill_ids=skill_ids,
            skill_tags=skill_tags,
            task_type=task_type,
            learned_model_scores=learned_model_scores,
            context=(
                [{"role": "system", "content": skill_context}]
                if skill_context
                else None
            ),
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
