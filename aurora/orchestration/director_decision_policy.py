"""Decision-policy evidence derived from durable Director outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from .director_decision_performance import DirectorDecisionPerformance


@dataclass(frozen=True)
class DirectorDecisionGuidance:
    decision: str
    evaluations: int
    success_rate: float
    average_score: float


class DirectorDecisionPolicy:
    """Convert observed decision outcomes into explicit advisory guidance.

    The policy never chooses a Director decision. It only exposes empirical
    evidence so the Director can incorporate it into the next review.
    """

    @staticmethod
    def guidance(
        performance: tuple[DirectorDecisionPerformance, ...],
    ) -> tuple[DirectorDecisionGuidance, ...]:
        return tuple(
            DirectorDecisionGuidance(
                decision=item.decision,
                evaluations=item.evaluations,
                success_rate=item.success_rate,
                average_score=item.average_score,
            )
            for item in performance
        )

    @staticmethod
    def prompt(guidance: tuple[DirectorDecisionGuidance, ...]) -> str:
        if not guidance:
            return ""
        return (
            "Director decision-policy evidence (advisory; do not treat as an "
            "automatic decision rule): "
            + "; ".join(
                f"{item.decision}: evaluations={item.evaluations}, "
                f"success_rate={item.success_rate:.3f}, "
                f"average_score={item.average_score:.3f}"
                for item in guidance
            )
        )


__all__ = ["DirectorDecisionGuidance", "DirectorDecisionPolicy"]
