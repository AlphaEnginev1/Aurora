"""Closed-loop discovery built on the existing ResearchService and Learning stores."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


class DiscoveryLoopError(RuntimeError):
    """Raised when a discovery cycle is invalid."""


@dataclass(frozen=True)
class DiscoveryCycle:
    cycle: int
    hypothesis: str
    evidence: tuple[str, ...]
    verdict: str
    novelty: str


@dataclass(frozen=True)
class DiscoveryResult:
    status: str
    cycles: tuple[DiscoveryCycle, ...]
    knowledge: tuple[str, ...]


class DiscoveryLoop:
    """Hypothesis -> evidence -> falsification -> novelty -> knowledge loop."""

    def __init__(
        self,
        *,
        investigate: Callable[[str], Mapping[str, Any]],
        hypothesize: Callable[[str, Mapping[str, Any]], str],
        falsify: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
        novelty: Callable[[str, Mapping[str, Any]], str],
        learn: Callable[[str, Mapping[str, Any]], str | None],
        max_cycles: int = 6,
    ) -> None:
        if max_cycles < 1 or max_cycles > 32:
            raise DiscoveryLoopError("max_cycles must be between 1 and 32")
        self.investigate = investigate
        self.hypothesize = hypothesize
        self.falsify = falsify
        self.novelty = novelty
        self.learn = learn
        self.max_cycles = max_cycles

    def run(self, objective: str) -> DiscoveryResult:
        objective = objective.strip()
        if not objective:
            raise DiscoveryLoopError("objective is empty")
        cycles: list[DiscoveryCycle] = []
        knowledge: list[str] = []
        evidence: Mapping[str, Any] = {}
        hypothesis = objective
        for cycle in range(1, self.max_cycles + 1):
            evidence = self.investigate(hypothesis)
            hypothesis = self.hypothesize(hypothesis, evidence).strip()
            if not hypothesis:
                raise DiscoveryLoopError("hypothesis generator returned an empty hypothesis")
            falsification = self.falsify(hypothesis, evidence)
            verdict = str(falsification.get("verdict", "insufficient")).strip().lower()
            novelty_state = self.novelty(hypothesis, falsification).strip().lower()
            cycles.append(
                DiscoveryCycle(
                    cycle=cycle,
                    hypothesis=hypothesis,
                    evidence=tuple(str(x) for x in falsification.get("evidence", ())),
                    verdict=verdict,
                    novelty=novelty_state,
                )
            )
            if verdict == "falsified":
                continue
            if verdict == "verified" and novelty_state in {"novel", "new"}:
                artifact = self.learn(hypothesis, falsification)
                # A positive model verdict is not a persisted learning result.
                # Do not report discovery unless the learning boundary returns
                # a durable artifact identifier.
                if artifact:
                    knowledge.append(artifact)
                    return DiscoveryResult("discovered", tuple(cycles), tuple(knowledge))
            if verdict in {"blocked", "insufficient"}:
                continue
        return DiscoveryResult("insufficient", tuple(cycles), tuple(knowledge))


__all__ = ["DiscoveryCycle", "DiscoveryLoop", "DiscoveryLoopError", "DiscoveryResult"]
