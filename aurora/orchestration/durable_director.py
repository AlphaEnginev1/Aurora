"""Durable Director submission for long-running Aurora missions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from uuid import uuid4

from .director import DirectorPlan, DirectorPlanner
from .event_bus import DurableEventBus
from .task_engine import DelegatedTask, TaskEngine
from .task_store import DurableTaskStore
from .team_runtime import TeamCoordinator, TeamCoordinatorError


class DurableDirectorError(RuntimeError):
    """Raised when a Director mission cannot be durably submitted."""


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
    ) -> MissionSubmission:
        if not objective.strip():
            raise DurableDirectorError("mission objective is empty")
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
        try:
            mission = mission_id or f"mission-{uuid4().hex}"
            team_state = None
            if self.team_coordinator is not None:
                team_state = self.team_coordinator.prepare(
                    mission_id=mission,
                    objective=plan.objective,
                    tasks=plan.tasks,
                )
                validated = team_state.tasks
                self.event_bus.publish(
                    mission_id=mission,
                    event_type="team.formed",
                    payload={
                        "objective": plan.objective,
                        "roles": [member.role for member in team_state.team.members],
                        "required_skills": list(team_state.team.required_skills),
                        "covered_skills": list(team_state.team.covered_skills),
                    },
                )
            else:
                validated = self.task_engine.validate(plan.tasks)
            self.store.enqueue(mission, validated)
            self.event_bus.publish(
                mission_id=mission,
                event_type="mission_submitted",
                payload={
                    "objective": plan.objective,
                    "model": plan.model,
                    "task_ids": [task.task_id for task in validated],
                },
            )
        except Exception as exc:
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
