"""Provider-neutral autonomous computer-use action planning.

Builds on the existing local workstation gateway and Workbench.  The module
adds a bounded observe -> act -> observe controller without duplicating GUI
transport or policy enforcement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


class ComputerUseError(RuntimeError):
    """Raised when a computer-use loop cannot continue."""


@dataclass(frozen=True)
class ComputerObservation:
    value: Mapping[str, Any]


@dataclass(frozen=True)
class ComputerStep:
    action: Mapping[str, Any]
    observation: ComputerObservation | None = None


class AutonomousComputerUse:
    """Run a bounded computer-use loop through an existing Workbench callback."""

    def __init__(
        self,
        execute: Callable[[list[dict[str, Any]]], Mapping[str, Any]],
        *,
        observe: Callable[[], Mapping[str, Any]] | None = None,
        decide: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
        max_steps: int = 32,
    ) -> None:
        if max_steps < 1 or max_steps > 128:
            raise ComputerUseError("max_steps must be between 1 and 128")
        self.execute = execute
        self.observe = observe
        self.decide = decide
        self.max_steps = max_steps

    def run(self, objective: str) -> tuple[ComputerStep, ...]:
        if not objective.strip():
            raise ComputerUseError("objective is empty")
        if self.decide is None:
            raise ComputerUseError("computer-use decision callback is required")
        observation: Mapping[str, Any] = (
            self.observe() if self.observe is not None else {"objective": objective}
        )
        steps: list[ComputerStep] = []
        for _ in range(self.max_steps):
            decision = self.decide({"objective": objective, "observation": observation})
            if not decision:
                return tuple(steps)
            action = dict(decision)
            result = self.execute([action])
            # Observe the actual environment again after every action. Treating
            # the action receipt itself as the next observation caused the model
            # to plan from stale state and violated the advertised observe-act-
            # observe loop.
            if self.observe is not None:
                observation = dict(self.observe())
                observation["last_action_result"] = dict(result)
            else:
                observation = dict(result)
            steps.append(ComputerStep(action=action, observation=ComputerObservation(observation)))
            if bool(result.get("completed")):
                return tuple(steps)
        raise ComputerUseError("computer-use step budget exhausted")


__all__ = ["AutonomousComputerUse", "ComputerObservation", "ComputerStep", "ComputerUseError"]
