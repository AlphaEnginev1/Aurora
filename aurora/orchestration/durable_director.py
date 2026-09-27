"""Durable Director submission for long-running Aurora missions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .director import DirectorPlan, DirectorPlanner
from .event_bus import DurableEventBus
from .task_engine import TaskEngine
from .task_store import DurableTaskStore


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
    ) -> None:
        self.planner = planner
        self.task_engine = task_engine
        self.store = store
        self.event_bus = event_bus or DurableEventBus(store)

    def submit_mission(
        self,
        *,
        objective: str,
        mission_id: str | None = None,
        context: list[dict] | None = None,
        model: str | None = None,
    ) -> MissionSubmission:
        plan = self.planner.plan(
            objective=objective,
            context=context,
            model=model,
        )
        try:
            validated = self.task_engine.validate(plan.tasks)
            mission = mission_id or f"mission-{uuid4().hex}"
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
        return MissionSubmission(mission_id=mission, plan=plan)


__all__ = ["DurableDirectorError", "DurableDirectorService", "MissionSubmission"]
