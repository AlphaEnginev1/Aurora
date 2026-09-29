"""Durable learning contracts for Director decisions.

Decision learning is event-backed and deliberately distinguishes observed
outcomes from stronger causal claims. A successful outcome is not treated as
proof that the Director decision was optimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .task_store import TaskEvent


class DirectorDecisionLearningError(ValueError):
    """Raised when a Director decision learning record is invalid."""


@dataclass(frozen=True)
class DirectorDecisionEvaluation:
    decision_event_id: int
    round: int
    decision: str
    score: float
    outcome: str
    evidence: tuple[str, ...]
    quality: str
    evidence_state: str
    attribution: str


class DirectorDecisionLearning:
    """Evaluate decisions without claiming unsupported causal certainty."""

    DECISIONS = frozenset({"complete", "continue", "blocked"})

    @classmethod
    def evaluate(
        cls,
        *,
        decision_event: TaskEvent,
        observed_event_types: Iterable[str],
    ) -> DirectorDecisionEvaluation | None:
        if decision_event.event_type != "director_review":
            raise DirectorDecisionLearningError("decision_event must be director_review")
        decision = decision_event.payload.get("decision")
        if decision not in cls.DECISIONS:
            raise DirectorDecisionLearningError(f"invalid Director decision: {decision!r}")

        observed = tuple(observed_event_types)
        if decision == "continue":
            if "mission_reopened" not in observed and not any(
                item in observed for item in ("mission_succeeded", "mission_failed", "mission_blocked")
            ):
                return None
            if "mission_failed" in observed or "mission_blocked" in observed:
                score, outcome = 0.0, "follow_up_failed"
                quality = "correction_required"
            else:
                score, outcome = 1.0, "follow_up_succeeded"
                quality = "supported_success"
        elif decision == "complete":
            if "mission_succeeded" not in observed:
                return None
            score, outcome = 1.0, "mission_completed"
            quality = "supported_success"
        else:
            if "mission_blocked" not in observed:
                return None
            score, outcome = 1.0, "mission_blocked_as_decided"
            quality = "supported_block"

        # The event stream proves temporal outcome alignment, not counterfactual
        # optimality. Keep attribution explicitly bounded to observed evidence.
        evidence_state = "outcome_observed"
        attribution = "temporal_outcome_alignment"

        return DirectorDecisionEvaluation(
            decision_event_id=decision_event.event_id,
            round=int(decision_event.payload.get("round", 0)),
            decision=decision,
            score=score,
            outcome=outcome,
            evidence=observed,
            quality=quality,
            evidence_state=evidence_state,
            attribution=attribution,
        )

    @staticmethod
    def learned_evidence(events: Iterable[TaskEvent]) -> list[dict]:
        evidence: list[dict] = []
        for event in events:
            if event.event_type != "director_decision_evaluated":
                continue
            evidence.append(
                {
                    "decision_event_id": event.payload.get("decision_event_id"),
                    "round": event.payload.get("round"),
                    "decision": event.payload.get("decision"),
                    "score": event.payload.get("score"),
                    "outcome": event.payload.get("outcome"),
                    "quality": event.payload.get("quality"),
                    "evidence_state": event.payload.get("evidence_state"),
                    "attribution": event.payload.get("attribution"),
                    "evidence": list(event.payload.get("evidence") or []),
                }
            )
        return evidence


__all__ = [
    "DirectorDecisionEvaluation",
    "DirectorDecisionLearning",
    "DirectorDecisionLearningError",
]
