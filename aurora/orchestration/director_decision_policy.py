"""Decision-policy evidence derived from durable Director outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from .director_decision_performance import DirectorDecisionPerformance
from .director_decision_policy_active_store import DirectorDecisionPolicyActive
from .director_decision_policy_evolution import DirectorDecisionPolicyProposal


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
    def active_prompt(
        active: DirectorDecisionPolicyActive,
        proposal: DirectorDecisionPolicyProposal,
    ) -> str:
        if proposal.proposal_id != active.proposal_id:
            raise ValueError("active policy proposal does not match active registry")
        if proposal.state != "proposed":
            raise ValueError("active registry may only reference a governed proposal")
        return (
            "Active Director policy guidance (governed and advisory; it does not "
            "override current evidence or automatically choose a decision): "
            f"proposal_id={proposal.proposal_id}; "
            f"observed_direction={proposal.observed_direction}; "
            f"evidence={proposal.evidence_summary}; "
            f"proposed_change={proposal.proposed_change}. "
            "Apply this guidance only when the current mission context is materially "
            "comparable to the evidence that produced the policy. Re-evaluate current "
            "evidence before deciding."
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
