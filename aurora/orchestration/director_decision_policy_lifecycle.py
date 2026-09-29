"""Governed lifecycle for Director decision-policy evolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from .director_decision_policy_evolution import (
    DirectorDecisionPolicyEvolutionError,
    DirectorDecisionPolicyProposal,
)


class DirectorDecisionPolicyLifecycleError(ValueError):
    """Raised when a policy lifecycle transition violates governance."""


@dataclass(frozen=True)
class DirectorDecisionPolicyValidation:
    validation_id: str
    proposal_id: str
    holdout_observations: int
    baseline_consistent: bool
    candidate_consistent: bool
    canary_eligible: bool
    reason: str
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.holdout_observations < 1:
            raise DirectorDecisionPolicyLifecycleError(
                "validation requires holdout observations"
            )
        if not self.reason.strip():
            raise DirectorDecisionPolicyLifecycleError("validation requires a reason")
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(timezone.utc).isoformat(),
            )


@dataclass(frozen=True)
class DirectorDecisionPolicyActivation:
    proposal_id: str
    validation_id: str
    state: str
    authorized_by: str
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.state not in {"canary", "active"}:
            raise DirectorDecisionPolicyLifecycleError(
                "activation state must be canary or active"
            )
        if not self.authorized_by.strip():
            raise DirectorDecisionPolicyLifecycleError("activation requires an authority")
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(timezone.utc).isoformat(),
            )


class DirectorDecisionPolicyLifecycle:
    """Validate and transition proposals without allowing evidence to self-activate."""

    @staticmethod
    def validate(
        proposal: DirectorDecisionPolicyProposal,
        *,
        holdout_observations: int,
        baseline_consistent: bool,
        candidate_consistent: bool,
        created_at: str | None = None,
    ) -> DirectorDecisionPolicyValidation:
        if proposal.state != "proposed":
            raise DirectorDecisionPolicyLifecycleError(
                "only proposed policy changes may be validated"
            )
        if holdout_observations < 2:
            return DirectorDecisionPolicyValidation(
                validation_id="",
                proposal_id=proposal.proposal_id,
                holdout_observations=holdout_observations,
                baseline_consistent=baseline_consistent,
                candidate_consistent=candidate_consistent,
                canary_eligible=False,
                reason="insufficient_holdout_observations",
                created_at=created_at or "",
            )
        eligible = baseline_consistent and candidate_consistent
        reason = "validated_for_canary" if eligible else "validation_consistency_failed"
        validation_id = "dpv-" + hashlib.sha256(
            (
                f"{proposal.proposal_id}|{holdout_observations}|"
                f"{baseline_consistent}|{candidate_consistent}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        return DirectorDecisionPolicyValidation(
            validation_id=validation_id,
            proposal_id=proposal.proposal_id,
            holdout_observations=holdout_observations,
            baseline_consistent=baseline_consistent,
            candidate_consistent=candidate_consistent,
            canary_eligible=eligible,
            reason=reason,
            created_at=created_at or "",
        )

    @staticmethod
    def enter_canary(
        proposal: DirectorDecisionPolicyProposal,
        validation: DirectorDecisionPolicyValidation,
        *,
        authorized_by: str,
        created_at: str | None = None,
    ) -> DirectorDecisionPolicyActivation:
        if proposal.state != "proposed":
            raise DirectorDecisionPolicyLifecycleError(
                "proposal must be proposed before canary activation"
            )
        if validation.proposal_id != proposal.proposal_id:
            raise DirectorDecisionPolicyLifecycleError(
                "validation does not belong to proposal"
            )
        if not validation.canary_eligible:
            raise DirectorDecisionPolicyLifecycleError(
                "proposal is not eligible for canary"
            )
        return DirectorDecisionPolicyActivation(
            proposal_id=proposal.proposal_id,
            validation_id=validation.validation_id,
            state="canary",
            authorized_by=authorized_by,
            created_at=created_at or "",
        )

    @staticmethod
    def activate(
        activation: DirectorDecisionPolicyActivation,
        *,
        authorization_granted: bool,
        created_at: str | None = None,
    ) -> DirectorDecisionPolicyActivation:
        if activation.state != "canary":
            raise DirectorDecisionPolicyLifecycleError(
                "only canary policies may become active"
            )
        if not authorization_granted:
            raise DirectorDecisionPolicyLifecycleError(
                "explicit policy authorization is required"
            )
        return DirectorDecisionPolicyActivation(
            proposal_id=activation.proposal_id,
            validation_id=activation.validation_id,
            state="active",
            authorized_by=activation.authorized_by,
            created_at=created_at or "",
        )


__all__ = [
    "DirectorDecisionPolicyActivation",
    "DirectorDecisionPolicyLifecycle",
    "DirectorDecisionPolicyLifecycleError",
    "DirectorDecisionPolicyValidation",
]
