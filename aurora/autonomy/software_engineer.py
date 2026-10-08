"""Autonomous software-engineering loop built on Aurora's existing Workbench.

This is deliberately an orchestration layer: Director, Workbench, Router,
GitHub and sandbox remain the implementation boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


class SoftwareEngineeringError(RuntimeError):
    """Raised when an autonomous engineering mission cannot continue."""


@dataclass(frozen=True)
class EngineeringIteration:
    number: int
    objective: str
    result: Mapping[str, Any]


@dataclass(frozen=True)
class EngineeringMissionResult:
    status: str
    iterations: tuple[EngineeringIteration, ...]
    final: Mapping[str, Any]


class AutonomousSoftwareEngineer:
    """Execute inspect -> plan -> edit -> test -> repair -> verify -> publish."""

    def __init__(
        self,
        *,
        execute: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
        evaluate: Callable[[Mapping[str, Any]], str],
        max_iterations: int = 8,
    ) -> None:
        if max_iterations < 1 or max_iterations > 32:
            raise SoftwareEngineeringError("max_iterations must be between 1 and 32")
        self.execute = execute
        self.evaluate = evaluate
        self.max_iterations = max_iterations

    def run(self, objective: str, *, context: Mapping[str, Any] | None = None) -> EngineeringMissionResult:
        objective = objective.strip()
        if not objective:
            raise SoftwareEngineeringError("objective is empty")
        state: dict[str, Any] = dict(context or {})
        iterations: list[EngineeringIteration] = []
        phases: Sequence[str] = (
            "inspect",
            "research",
            "plan",
            "edit",
            "test",
            "verify",
            "publish",
        )
        for number in range(1, self.max_iterations + 1):
            phase = phases[min(number - 1, len(phases) - 1)]
            result = dict(self.execute(phase, {"objective": objective, **state}))
            state.update(result)
            iterations.append(EngineeringIteration(number, objective, result))
            verdict = self.evaluate(state).strip().lower()
            if verdict in {"succeeded", "success", "complete", "completed"}:
                return EngineeringMissionResult("succeeded", tuple(iterations), state)
            if verdict in {"blocked", "failed"} and phase == "verify":
                return EngineeringMissionResult(verdict, tuple(iterations), state)
            if phase == "publish" and verdict not in {"continue", "repair"}:
                return EngineeringMissionResult("blocked", tuple(iterations), state)
        return EngineeringMissionResult("blocked", tuple(iterations), state)


__all__ = [
    "AutonomousSoftwareEngineer",
    "EngineeringIteration",
    "EngineeringMissionResult",
    "SoftwareEngineeringError",
]
