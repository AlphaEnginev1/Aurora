"""Durable learning contracts for Director decisions.

Decision learning distinguishes observed outcomes from evidence sufficiency and
stronger causal claims. A successful outcome is not proof that the decision was
optimal, and incomplete evidence is never converted into a scored evaluation.
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
    score: float | None
    outcome: str
    evidence: tuple[str, ...]
    quality: str
    evidence_state: str
    attribution: str
    evidence_completeness: str
    sufficiency_reasons: tuple[str, ...]


class DirectorDecisionLearning:
    """Evaluate decisions without claiming unsupported causal certainty."""

    DECISIONS = frozenset({"complete", "continue", "blocked"})
    EVIDENCE_COMPLETENESS = frozenset({"complete", "partial", "insufficient"})

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

        observed = tuple(dict.fromkeys(observed_event_types))
        observed_set = set(observed)
        has_execution = "director_decision_executed" in observed_set
        terminal = next(
            (item for item in ("mission_succeeded", "mission_failed", "mission_blocked") if item in observed_set),
            None,
        )
        has_post_task_outcome = any(
            item in observed_set
            for item in ("task_completed", "task_failed", "task_blocked")
        )
        reasons: list[str] = []
        outcome: str
        score: float | None
        quality: str
        attribution = "temporal_outcome_alignment"

        if terminal is None:
            return None

        reasons.append("result_observed")
        if has_execution:
            reasons.append("execution_observed")
        else:
            reasons.append("execution_evidence_missing")

        if decision == "continue":
            if "mission_reopened" in observed_set:
                reasons.append("follow_up_reopened")
            else:
                reasons.append("follow_up_reopen_evidence_missing")
            if has_post_task_outcome:
                reasons.append("post_follow_up_task_outcome_observed")
            else:
                reasons.append("post_follow_up_task_outcome_missing")

            if terminal == "mission_failed" or terminal == "mission_blocked":
                outcome = "follow_up_failed"
                score = 0.0
                quality = "correction_required"
            else:
                outcome = "follow_up_succeeded"
                score = 1.0
                quality = "supported_success"

            required = has_execution and "mission_reopened" in observed_set and has_post_task_outcome
        elif decision == "complete":
            outcome = "mission_completed" if terminal == "mission_succeeded" else f"mission_{terminal.removeprefix('mission_')}"
            score = 1.0 if terminal == "mission_succeeded" else 0.0
            quality = "supported_success" if terminal == "mission_succeeded" else "correction_required"
            required = has_execution
        else:
            outcome = "mission_blocked_as_decided" if terminal == "mission_blocked" else f"mission_{terminal.removeprefix('mission_')}_as_observed"
            score = 1.0 if terminal == "mission_blocked" else 0.0
            quality = "supported_block" if terminal == "mission_blocked" else "correction_required"
            required = has_execution

        if required:
            completeness = "complete"
            evidence_state = "outcome_observed"
        elif has_execution or has_post_task_outcome:
            completeness = "partial"
            evidence_state = "evaluation_pending"
            score = None
            quality = "evaluation_pending"
        else:
            completeness = "insufficient"
            evidence_state = "insufficient_evidence"
            score = None
            quality = "evaluation_pending"

        if completeness != "complete":
            attribution = "insufficient_evidence_for_quality_attribution"

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
            evidence_completeness=completeness,
            sufficiency_reasons=tuple(reasons),
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
                    "evidence_completeness": event.payload.get("evidence_completeness"),
                    "sufficiency_reasons": list(event.payload.get("sufficiency_reasons") or []),
                    "evidence": list(event.payload.get("evidence") or []),
                }
            )
        return evidence


__all__ = [
    "DirectorDecisionEvaluation",
    "DirectorDecisionLearning",
    "DirectorDecisionLearningError",
]
