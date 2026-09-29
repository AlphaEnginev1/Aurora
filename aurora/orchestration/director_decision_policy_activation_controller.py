"""Governed controller for promoting a Director policy canary to active state."""

from __future__ import annotations

from .director_decision_policy_active_store import (
    DirectorDecisionPolicyActive,
    DirectorDecisionPolicyActiveStore,
)
from .director_decision_policy_canary_evaluation import (
    DirectorDecisionPolicyCanaryEvaluation,
)
from .director_decision_policy_lifecycle import (
    DirectorDecisionPolicyActivation,
    DirectorDecisionPolicyLifecycle,
    DirectorDecisionPolicyLifecycleError,
)


class DirectorDecisionPolicyActivationController:
    """Bridge canary evidence to the durable active-policy registry."""

    def __init__(self, active_store: DirectorDecisionPolicyActiveStore) -> None:
        self.active_store = active_store

    def activate(
        self,
        canary: DirectorDecisionPolicyActivation,
        evaluation: DirectorDecisionPolicyCanaryEvaluation,
        *,
        authorization_granted: bool,
        created_at: str | None = None,
    ) -> DirectorDecisionPolicyActivation:
        if canary.state != "canary":
            raise DirectorDecisionPolicyLifecycleError(
                "policy activation requires a canary-state activation"
            )
        if evaluation.proposal_id != canary.proposal_id:
            raise DirectorDecisionPolicyLifecycleError(
                "canary evaluation does not belong to activation proposal"
            )
        if evaluation.rollback_required:
            raise DirectorDecisionPolicyLifecycleError(
                "policy with required canary rollback cannot become active"
            )
        if not evaluation.governance_review_ready:
            raise DirectorDecisionPolicyLifecycleError(
                "canary evidence is not ready for governance review"
            )
        activated = DirectorDecisionPolicyLifecycle.activate(
            canary,
            authorization_granted=authorization_granted,
            created_at=created_at,
        )
        self.active_store.activate(
            DirectorDecisionPolicyActive(
                proposal_id=activated.proposal_id,
                validation_id=activated.validation_id,
                authorized_by=activated.authorized_by,
                activated_at=activated.created_at,
            )
        )
        return activated

    def rollback_active(
        self,
        active: DirectorDecisionPolicyActivation,
        *,
        reason: str,
        authorized_by: str,
        created_at: str | None = None,
    ) -> DirectorDecisionPolicyActivation:
        if active.state != "active":
            raise DirectorDecisionPolicyLifecycleError(
                "only an active policy may use active rollback"
            )
        rolled_back = DirectorDecisionPolicyLifecycle.rollback(
            active,
            reason=reason,
            authorized_by=authorized_by,
            created_at=created_at,
        )
        self.active_store.deactivate(
            proposal_id=active.proposal_id,
            reason=reason,
            deactivated_at=rolled_back.created_at,
        )
        return rolled_back


__all__ = ["DirectorDecisionPolicyActivationController"]
