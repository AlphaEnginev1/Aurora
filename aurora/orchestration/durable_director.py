"""Durable Director submission for long-running Aurora missions."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Sequence
from uuid import uuid4

from .director import DirectorPlan, DirectorPlanner
from .event_bus import DurableEventBus
from .task_engine import DelegatedTask, TaskEngine
from .task_store import DurableTaskStore
from .team_runtime import TeamCoordinator, TeamCoordinatorError


class DurableDirectorError(RuntimeError):
    """Raised when a Director mission cannot be durably submitted."""


def _task_graph_payload(tasks: Sequence[DelegatedTask]) -> list[dict[str, object]]:
    return [
        {
            "task_id": task.task_id,
            "role": task.role,
            "objective": task.objective,
            "capability": task.capability,
            "args": list(task.args),
            "depends_on": list(task.depends_on),
            "skills": list(task.skills),
            "project_id": task.project_id,
        }
        for task in tasks
    ]


def _task_graph_fingerprint(tasks: Sequence[DelegatedTask]) -> str:
    serialized = json.dumps(
        _task_graph_payload(tasks),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MissionSubmission:
    mission_id: str
    plan: DirectorPlan


class DurableDirectorService:
    """Plan a mission once, persist its task graph, then let workers execute it."""

    def __init__(
        self,
        *,
        planner: DirectorPlanner,
        task_engine: TaskEngine,
        store: DurableTaskStore,
        event_bus: DurableEventBus | None = None,
        team_coordinator: TeamCoordinator | None = None,
    ) -> None:
        self.planner = planner
        self.task_engine = task_engine
        self.store = store
        self.event_bus = event_bus or DurableEventBus(store)
        self.team_coordinator = team_coordinator

    def _restore_committed_submission(
        self,
        *,
        mission_id: str,
        objective: str,
        project_id: str | None,
        correlation_id: str | None,
        tasks: Sequence[DelegatedTask] | None,
        requested_fingerprint: str | None,
    ) -> MissionSubmission | None:
        """Read the one authoritative committed receipt for an idempotent order."""
        existing_tasks = self.store.list_tasks(mission_id)
        if not existing_tasks:
            return None
        existing_events = self.store.events(mission_id)
        submitted_event = next(
            (event for event in existing_events if event.event_type == "mission_submitted"),
            None,
        )
        if submitted_event is None:
            raise DurableDirectorError(
                "mission_id already has queued tasks but no authoritative submission receipt; "
                "refusing to create a duplicate mission"
            )
        submitted = submitted_event.payload
        if submitted.get("objective") != objective:
            raise DurableDirectorError(
                "mission_id is already bound to a different objective"
            )
        if submitted.get("project_id") != project_id:
            raise DurableDirectorError("mission_id is already bound to a different project_id")
        if tasks is not None and submitted.get("requested_tasks_fingerprint") != requested_fingerprint:
            raise DurableDirectorError(
                "mission_id is already bound to a different explicit task graph"
            )
        existing_correlation = submitted.get("correlation_id")
        if correlation_id and existing_correlation != correlation_id:
            raise DurableDirectorError(
                "mission_id is already bound to a different or missing correlation_id"
            )
        restored_tasks = tuple(item.task for item in existing_tasks)
        restored_plan = DirectorPlan(
            objective=str(submitted.get("objective", objective)),
            tasks=restored_tasks,
            model=str(submitted.get("model", "unknown")),
            provider=(
                str(submitted["provider"])
                if submitted.get("provider") is not None else None
            ),
            execution_id=(
                str(submitted["planning_execution_id"])
                if submitted.get("planning_execution_id") is not None else None
            ),
        )
        return MissionSubmission(mission_id=mission_id, plan=restored_plan)

    def submit_mission(
        self,
        *,
        objective: str,
        mission_id: str | None = None,
        context: list[dict] | None = None,
        model: str | None = None,
        tasks: Sequence[DelegatedTask] | None = None,
        planning_model: str | None = None,
        planning_provider: str | None = None,
        planning_execution_id: str | None = None,
        correlation_id: str | None = None,
        project_id: str | None = None,
    ) -> MissionSubmission:
        normalized_objective = objective.strip()
        if not normalized_objective:
            raise DurableDirectorError("mission objective is empty")
        if correlation_id is not None and (
            not correlation_id.strip() or "\x00" in correlation_id
        ):
            raise DurableDirectorError("correlation_id must be non-empty and contain no null byte")
        if project_id is not None and (
            not isinstance(project_id, str) or not project_id.strip() or "\x00" in project_id
        ):
            raise DurableDirectorError("project_id must be a non-empty identifier or null")
        project_id = project_id.strip() if project_id else None

        # A caller-supplied mission_id is the idempotency key. If the request was
        # already committed, return its durable receipt instead of invoking the
        # planner or attempting to enqueue the same task IDs again.
        requested_fingerprint = (
            _task_graph_fingerprint(tuple(tasks)) if tasks is not None else None
        )
        if mission_id and mission_id.strip():
            existing = self._restore_committed_submission(
                mission_id=mission_id.strip(),
                objective=normalized_objective,
                project_id=project_id,
                correlation_id=correlation_id,
                tasks=tasks,
                requested_fingerprint=requested_fingerprint,
            )
            if existing is not None:
                return existing

        if project_id:
            project_registry = getattr(self.planner, "project_registry", None)
            if project_registry is None:
                raise DurableDirectorError("project-scoped mission requires the canonical project registry")
            try:
                project = project_registry.resolve(project_id)
            except Exception as exc:
                raise DurableDirectorError(f"unknown project_id: {project_id}") from exc
            if tasks is None:
                context = list(context or [])
                context.append({"role": "system", "content": json.dumps({
                    "authoritative_project": {
                        "project_id": project.project_id,
                        "name": project.name,
                        "objective": project.objective,
                        "status": project.status.value if hasattr(project.status, "value") else str(project.status),
                        "repository": project.repository,
                        "workspace": project.workspace,
                        "architecture_ref": project.architecture_ref,
                        "current_state": project.current_state,
                    }
                }, ensure_ascii=False, sort_keys=True)})

        if tasks is None:
            plan = self.planner.plan(
                objective=objective,
                context=context,
                model=model,
            )
        else:
            # A provider may submit an explicit, declarative task graph. It is
            # still untrusted input: TaskEngine validation and policy/team checks
            # below are mandatory before anything is persisted or executed.
            plan = DirectorPlan(
                objective=objective.strip(),
                tasks=tuple(tasks),
                model=planning_model or "unknown",
                provider=planning_provider,
                execution_id=planning_execution_id,
            )

        # Task-level project identities can affect inference and Workbench
        # scope even when the mission itself is not project-bound. Resolve every
        # supplied identity against the canonical registry before any write.
        project_registry = getattr(self.planner, "project_registry", None)
        for task in plan.tasks:
            task_project_id = task.project_id
            if task_project_id is None:
                continue
            if project_id is not None and task_project_id != project_id:
                raise DurableDirectorError(
                    f"task {task.task_id!r} project_id conflicts with authoritative mission project"
                )
            if project_registry is None:
                raise DurableDirectorError(
                    f"task {task.task_id!r} is project-scoped but the canonical project registry is unavailable"
                )
            try:
                project_registry.resolve(task_project_id)
            except Exception as exc:
                raise DurableDirectorError(
                    f"task {task.task_id!r} references unknown project_id {task_project_id!r}"
                ) from exc
        try:
            mission = mission_id.strip() if mission_id and mission_id.strip() else f"mission-{uuid4().hex}"
            initial_events: list[dict[str, object]] = []
            team_state = None
            if self.team_coordinator is not None:
                team_state = self.team_coordinator.prepare(
                    mission_id=mission,
                    objective=plan.objective,
                    tasks=plan.tasks,
                )
                validated = team_state.tasks
                initial_events.append({
                    "event_type": "team.formed",
                    **({"project_id": project_id} if project_id is not None else {}),
                    "correlation_id": correlation_id,
                    "payload": {
                        "objective": plan.objective,
                        **({"project_id": project_id} if project_id is not None else {}),
                        "roles": [member.role for member in team_state.team.members],
                        "required_skills": list(team_state.team.required_skills),
                        "covered_skills": list(team_state.team.covered_skills),
                    },
                })
            else:
                validated = self.task_engine.validate(plan.tasks)
            if project_id:
                validated = tuple(replace(task, project_id=project_id) for task in validated)
            initial_events.append({
                "event_type": "mission_submitted",
                **({"project_id": project_id} if project_id is not None else {}),
                "correlation_id": correlation_id,
                "payload": {
                    "objective": plan.objective,
                    "model": plan.model,
                    "provider": plan.provider,
                    "planning_execution_id": plan.execution_id,
                    "correlation_id": correlation_id,
                    **({"project_id": project_id} if project_id is not None else {}),
                    "task_ids": [task.task_id for task in validated],
                    "requested_tasks_fingerprint": requested_fingerprint,
                    "task_graph": _task_graph_payload(validated),
                },
            })
            # Task records, per-task "enqueued" events, and the submission
            # receipt commit or roll back as one SQLite transaction.
            self.store.enqueue(
                mission,
                validated,
                initial_events=initial_events,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            # Concurrent callers can both miss the preflight read and plan
            # before one wins the atomic enqueue. If this caller loses the
            # uniqueness race, return the matching committed receipt rather
            # than reporting a false rejection for an already accepted order.
            if mission_id and mission_id.strip():
                restored = self._restore_committed_submission(
                    mission_id=mission_id.strip(),
                    objective=normalized_objective,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    tasks=tasks,
                    requested_fingerprint=requested_fingerprint,
                )
                if restored is not None:
                    return restored
            raise DurableDirectorError(
                f"mission could not be durably submitted: {exc}"
            ) from exc
        if validated != plan.tasks:
            plan = DirectorPlan(
                objective=plan.objective,
                tasks=validated,
                model=plan.model,
                provider=plan.provider,
                execution_id=plan.execution_id,
            )
        return MissionSubmission(mission_id=mission, plan=plan)


__all__ = ["DurableDirectorError", "DurableDirectorService", "MissionSubmission"]
