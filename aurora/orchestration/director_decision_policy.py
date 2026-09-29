"""Decision-policy evidence derived from durable Director outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import json

from .director_decision_performance import DirectorDecisionPerformance
from .director_decision_policy_active_store import DirectorDecisionPolicyActive
from .director_decision_policy_evolution import DirectorDecisionPolicyProposal
from .director_decision_policy_contract import DirectorDecisionPolicyContract


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
    def active_contract(
        active: DirectorDecisionPolicyActive,
        proposal: DirectorDecisionPolicyProposal,
    ) -> DirectorDecisionPolicyContract:
        if proposal.proposal_id != active.proposal_id:
            raise ValueError("active policy proposal does not match active registry")
        if proposal.state != "proposed":
            raise ValueError("active registry may only reference a governed proposal")
        return DirectorDecisionPolicyContract.from_proposal(
            proposal_id=proposal.proposal_id,
            validation_id=active.validation_id,
            knowledge_id=proposal.knowledge_id,
            alternative=proposal.alternative,
            chosen_decision=proposal.chosen_decision,
            observed_direction=proposal.observed_direction,
            comparability_basis=proposal.comparability_basis,
            proposed_change=proposal.proposed_change,
            evidence_summary=proposal.evidence_summary,
            authorized_by=active.authorized_by,
        )

    @staticmethod
    def active_prompt(
        active: DirectorDecisionPolicyActive,
        proposal: DirectorDecisionPolicyProposal,
    ) -> str:
        contract = DirectorDecisionPolicy.active_contract(active, proposal)
        return (
            "Active Director policy guidance and contract (governed and advisory; never an "
            "automatic decision rule): "
            + json.dumps(contract.advisory_record(), sort_keys=True)
            + f" proposed_change={proposal.proposed_change}. "
            + "The active guidance does not override current evidence and must "
              "never automatically choose a decision. Apply only when the current "
              "mission context is materially comparable to the governed evidence, "
              "then re-evaluate current evidence before deciding."
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
