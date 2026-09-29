"""Governed shadow-canary observations for Director decision policy candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from .director_decision_policy_lifecycle import (
    DirectorDecisionPolicyActivation,
    DirectorDecisionPolicyLifecycleError,
)


class DirectorDecisionPolicyCanaryError(ValueError):
    """Raised when a shadow-canary observation violates governance."""


@dataclass(frozen=True)
class DirectorDecisionPolicyShadowObservation:
    observation_id: str
    proposal_id: str
    validation_id: str
    decision_event_id: int
    baseline_decision: str
    candidate_decision: str
    actual_outcome: str
    decision_agreement: bool
    baseline_executed: bool = True
    candidate_executed: bool = False
    state: str = "shadow_observed"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.observation_id.strip():
            raise DirectorDecisionPolicyCanaryError("shadow observation requires an id")
        if not self.proposal_id.strip() or not self.validation_id.strip():
            raise DirectorDecisionPolicyCanaryError(
                "shadow observation requires proposal and validation ids"
            )
        if self.decision_event_id < 1:
            raise DirectorDecisionPolicyCanaryError(
                "shadow observation requires a durable decision event id"
            )
        if not self.baseline_decision.strip() or not self.candidate_decision.strip():
            raise DirectorDecisionPolicyCanaryError(
                "shadow observation requires baseline and candidate decisions"
            )
        if not self.actual_outcome.strip():
            raise DirectorDecisionPolicyCanaryError(
                "shadow observation requires an observed baseline outcome"
            )
        if not self.baseline_executed:
            raise DirectorDecisionPolicyCanaryError(
                "shadow canary requires the baseline policy to be the executed policy"
            )
        if self.candidate_executed:
            raise DirectorDecisionPolicyCanaryError(
                "shadow canary candidate must never execute"
            )
        if self.state != "shadow_observed":
            raise DirectorDecisionPolicyCanaryError(
                "shadow observation state must remain shadow_observed"
            )
        expected_agreement = self.baseline_decision == self.candidate_decision
        if self.decision_agreement != expected_agreement:
            raise DirectorDecisionPolicyCanaryError(
                "decision_agreement does not match baseline/candidate decisions"
            )
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(timezone.utc).isoformat(),
            )


class DirectorDecisionPolicyShadowCanary:
    """Record a live baseline decision with a non-executing candidate shadow."""

    @staticmethod
    def observe(
        activation: DirectorDecisionPolicyActivation,
        *,
        decision_event_id: int,
        baseline_decision: str,
        candidate_decision: str,
        actual_outcome: str,
        created_at: str | None = None,
    ) -> DirectorDecisionPolicyShadowObservation:
        if activation.state != "canary":
            raise DirectorDecisionPolicyCanaryError(
                "shadow observation requires a policy activation in canary state"
            )
        if not activation.validation_id.strip():
            raise DirectorDecisionPolicyCanaryError(
                "canary activation requires a validation id"
            )
        baseline = baseline_decision.strip()
        candidate = candidate_decision.strip()
        outcome = actual_outcome.strip()
        if not baseline or not candidate or not outcome:
            raise DirectorDecisionPolicyCanaryError(
                "shadow observation inputs cannot be empty"
            )
        observation_id = "dpo-" + hashlib.sha256(
            (
                f"{activation.proposal_id}|{activation.validation_id}|"
                f"{decision_event_id}|{baseline}|{candidate}|{outcome}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        return DirectorDecisionPolicyShadowObservation(
            observation_id=observation_id,
            proposal_id=activation.proposal_id,
            validation_id=activation.validation_id,
            decision_event_id=decision_event_id,
            baseline_decision=baseline,
            candidate_decision=candidate,
            actual_outcome=outcome,
            decision_agreement=baseline == candidate,
            baseline_executed=True,
            candidate_executed=False,
            created_at=created_at or "",
        )

    @staticmethod
    def boundary(observation: DirectorDecisionPolicyShadowObservation) -> str:
        if observation.candidate_executed:
            raise DirectorDecisionPolicyCanaryError(
                "shadow candidate execution is forbidden"
            )
        return "candidate_is_shadow_only"


__all__ = [
    "DirectorDecisionPolicyCanaryError",
    "DirectorDecisionPolicyShadowCanary",
    "DirectorDecisionPolicyShadowObservation",
]
