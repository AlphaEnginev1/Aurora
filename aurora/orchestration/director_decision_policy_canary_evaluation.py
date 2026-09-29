"""Formal evaluation of Director policy shadow-canary observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from .director_decision_policy_canary_store import DirectorDecisionPolicyShadowCanaryStore


class DirectorDecisionPolicyCanaryEvaluationError(ValueError):
    """Raised when canary evaluation inputs are invalid."""


@dataclass(frozen=True)
class DirectorDecisionPolicyCanaryEvaluation:
    evaluation_id: str
    proposal_id: str
    observations: int
    agreements: int
    disagreements: int
    agreement_rate: float
    governance_review_ready: bool
    rollback_required: bool
    state: str
    reason: str
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.evaluation_id.strip():
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "canary evaluation requires an id"
            )
        if not self.proposal_id.strip():
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "canary evaluation requires a proposal id"
            )
        if self.observations < 0 or self.agreements < 0 or self.disagreements < 0:
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "canary evaluation counts cannot be negative"
            )
        if self.agreements + self.disagreements != self.observations:
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "canary evaluation counts are inconsistent"
            )
        if not 0.0 <= self.agreement_rate <= 1.0:
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "agreement rate must be between zero and one"
            )
        if self.state not in {"governance_review", "rollback_required"}:
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "invalid canary evaluation state"
            )
        if not self.reason.strip():
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "canary evaluation requires a reason"
            )
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(timezone.utc).isoformat(),
            )


class DirectorDecisionPolicyCanaryEvaluator:
    """Evaluate shadow evidence without making a causal performance claim."""

    @staticmethod
    def evaluate(
        store: DirectorDecisionPolicyShadowCanaryStore,
        proposal_id: str,
        *,
        min_observations: int = 5,
        created_at: str | None = None,
    ) -> DirectorDecisionPolicyCanaryEvaluation:
        if not proposal_id.strip():
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "proposal id cannot be empty"
            )
        if min_observations < 1:
            raise DirectorDecisionPolicyCanaryEvaluationError(
                "min_observations must be at least 1"
            )
        summary = store.summarize_for_proposal(proposal_id)
        agreement_rate = (
            summary.agreements / summary.observations
            if summary.observations
            else 0.0
        )
        rollback_required = summary.candidate_execution_attempts > 0
        state = "rollback_required" if rollback_required else "governance_review"
        reason = (
            "candidate_execution_detected"
            if rollback_required
            else (
                "insufficient_shadow_observations"
                if summary.observations < min_observations
                else "shadow_evidence_ready_for_governance_review"
            )
        )
        evaluation_id = "dpe-" + hashlib.sha256(
            (
                f"{proposal_id}|{summary.observations}|{summary.agreements}|"
                f"{summary.disagreements}|{summary.candidate_execution_attempts}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        return DirectorDecisionPolicyCanaryEvaluation(
            evaluation_id=evaluation_id,
            proposal_id=proposal_id,
            observations=summary.observations,
            agreements=summary.agreements,
            disagreements=summary.disagreements,
            agreement_rate=agreement_rate,
            governance_review_ready=(
                not rollback_required and summary.observations >= min_observations
            ),
            rollback_required=rollback_required,
            state=state,
            reason=reason,
            created_at=created_at or "",
        )


__all__ = [
    "DirectorDecisionPolicyCanaryEvaluation",
    "DirectorDecisionPolicyCanaryEvaluationError",
    "DirectorDecisionPolicyCanaryEvaluator",
]
