"""Governed proposals for evolving Director decision policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from .director_decision_knowledge import DirectorDecisionKnowledge


class DirectorDecisionPolicyEvolutionError(ValueError):
    """Raised when a policy proposal violates governance boundaries."""


@dataclass(frozen=True)
class DirectorDecisionPolicyProposal:
    proposal_id: str
    knowledge_id: str
    alternative: str
    chosen_decision: str
    observed_direction: str
    proposed_change: str
    evidence_summary: str
    state: str = "proposed"
    created_at: str = ""
    comparability_basis: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state != "proposed":
            raise DirectorDecisionPolicyEvolutionError(
                "new policy proposals must remain proposed until explicitly approved"
            )
        if not self.knowledge_id.strip() or not self.proposal_id.strip():
            raise DirectorDecisionPolicyEvolutionError("policy proposal requires durable identifiers")
        if self.observed_direction not in {"better", "worse", "same"}:
            raise DirectorDecisionPolicyEvolutionError("policy proposal requires determinate observed direction")
        if not self.proposed_change.strip() or not self.evidence_summary.strip():
            raise DirectorDecisionPolicyEvolutionError("policy proposal requires change and evidence")
        normalized_basis = tuple(
            sorted({item.strip() for item in self.comparability_basis if item.strip()})
        )
        if normalized_basis != self.comparability_basis:
            object.__setattr__(self, "comparability_basis", normalized_basis)
        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(timezone.utc).isoformat())


class DirectorDecisionPolicyEvolution:
    """Turn promoted empirical knowledge into reviewable policy proposals only."""

    @staticmethod
    def propose(
        knowledge: DirectorDecisionKnowledge,
        *,
        proposed_change: str,
        evidence_summary: str | None = None,
        created_at: str | None = None,
    ) -> DirectorDecisionPolicyProposal:
        if knowledge.state != "promoted_empirical":
            raise DirectorDecisionPolicyEvolutionError(
                "only promoted empirical knowledge may generate a policy proposal"
            )
        if not proposed_change.strip():
            raise DirectorDecisionPolicyEvolutionError("proposed policy change cannot be empty")

        proposal_id = "dpp-" + hashlib.sha256(
            (
                f"{knowledge.knowledge_id}|{proposed_change.strip()}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        summary = evidence_summary or knowledge.claim
        return DirectorDecisionPolicyProposal(
            proposal_id=proposal_id,
            knowledge_id=knowledge.knowledge_id,
            alternative=knowledge.alternative,
            chosen_decision=knowledge.chosen_decision,
            observed_direction=knowledge.observed_direction,
            proposed_change=proposed_change.strip(),
            evidence_summary=summary.strip(),
            comparability_basis=tuple(knowledge.comparability_basis),
            created_at=created_at or "",
        )

    @staticmethod
    def approval_boundary(proposal: DirectorDecisionPolicyProposal) -> str:
        """Describe what is required before policy can become active."""
        if proposal.state != "proposed":
            raise DirectorDecisionPolicyEvolutionError("invalid proposal state")
        return "explicit_policy_approval_required"
