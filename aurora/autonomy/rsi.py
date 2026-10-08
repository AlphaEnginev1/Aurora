"""Evidence-driven recursive self-improvement over Aurora projects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


class RSIError(RuntimeError):
    """Raised when a self-improvement cycle cannot be completed."""


@dataclass(frozen=True)
class RSICycle:
    cycle: int
    phase: str
    result: Mapping[str, Any]


@dataclass(frozen=True)
class RSIResult:
    status: str
    cycles: tuple[RSICycle, ...]
    change_id: str | None


class RecursiveSelfImprovement:
    """Detect -> research -> modify -> test -> CI -> learn using existing boundaries."""

    PHASES = ("inspect", "diagnose", "research", "modify", "test", "ci", "learn")

    def __init__(
        self,
        *,
        execute_phase: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
        evaluate: Callable[[Mapping[str, Any]], str],
        max_cycles: int = 3,
    ) -> None:
        if max_cycles < 1 or max_cycles > 12:
            raise RSIError("max_cycles must be between 1 and 12")
        self.execute_phase = execute_phase
        self.evaluate = evaluate
        self.max_cycles = max_cycles

    def run(self, objective: str, *, project: str = "Aurora") -> RSIResult:
        objective = objective.strip()
        project = project.strip()
        if not objective or not project:
            raise RSIError("objective and project are required")
        state: dict[str, Any] = {"objective": objective, "project": project}
        cycles: list[RSICycle] = []
        for cycle in range(1, self.max_cycles + 1):
            for phase in self.PHASES:
                result = dict(self.execute_phase(phase, state))
                state.update(result)
                cycles.append(RSICycle(cycle, phase, result))
            verdict = self.evaluate(state).strip().lower()
            if verdict in {"succeeded", "success", "improved"}:
                change_id = state.get("change_id")
                return RSIResult("succeeded", tuple(cycles), str(change_id) if change_id else None)
            if verdict in {"blocked", "failed"}:
                return RSIResult(verdict, tuple(cycles), None)
        return RSIResult("insufficient", tuple(cycles), None)


__all__ = ["RSICycle", "RSIResult", "RecursiveSelfImprovement", "RSIError"]
