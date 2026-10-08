"""Autonomous mission manager over the existing DirectorRuntime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


class MissionManagerError(RuntimeError):
    """Raised when an autonomous mission cannot be advanced."""


@dataclass(frozen=True)
class MissionRun:
    mission_id: str
    status: str
    cycles: int
    result: Any


class AutonomousMissionManager:
    """Turn a user objective into a durable Director mission and drive it to terminal."""

    def __init__(self, runtime, *, max_cycles: int = 64) -> None:
        if max_cycles < 1 or max_cycles > 1024:
            raise MissionManagerError("max_cycles must be between 1 and 1024")
        self.runtime = runtime
        self.max_cycles = max_cycles

    def run(
        self,
        objective: str,
        *,
        context: list[dict[str, Any]] | None = None,
        on_cycle: Callable[[Any], None] | None = None,
    ) -> MissionRun:
        objective = objective.strip()
        if not objective:
            raise MissionManagerError("objective is empty")
        submission = self.runtime.submit_mission(objective=objective, context=context)
        result = self.runtime.run_until_terminal(submission, max_cycles=self.max_cycles)
        if on_cycle is not None:
            on_cycle(result)
        return MissionRun(
            mission_id=str(submission.mission_id),
            status=str(result.status),
            cycles=self.max_cycles,
            result=result,
        )


__all__ = ["AutonomousMissionManager", "MissionManagerError", "MissionRun"]
