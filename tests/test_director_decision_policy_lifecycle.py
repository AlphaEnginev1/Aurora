import pytest

from aurora.orchestration.director_decision_knowledge import DirectorDecisionKnowledge
from aurora.orchestration.director_decision_policy_evolution import DirectorDecisionPolicyEvolution
from aurora.orchestration.director_decision_policy_lifecycle import (
    DirectorDecisionPolicyLifecycle,
    DirectorDecisionPolicyLifecycleError,
)


def _proposal():
    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-lifecycle",
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
        observed_direction="better",
        decision_events=2,
        observations=2,
        sources=("independent_execution",),
        claim="Repeated empirical observation.",
        created_at="2026-09-29T19:00:00+00:00",
    )
    return DirectorDecisionPolicyEvolution.propose(
        knowledge,
        proposed_change="Test complete in the recurring comparable context.",
        created_at="2026-09-29T19:01:00+00:00",
    )


def test_policy_lifecycle_validates_for_canary():
    proposal = _proposal()
    validation = DirectorDecisionPolicyLifecycle.validate(
        proposal,
        holdout_observations=4,
        baseline_consistent=True,
        candidate_consistent=True,
        created_at="2026-09-29T19:02:00+00:00",
    )
    assert validation.canary_eligible is True
    assert validation.reason == "validated_for_canary"
    assert validation.validation_id


def test_policy_lifecycle_rejects_inconsistent_validation():
    validation = DirectorDecisionPolicyLifecycle.validate(
        _proposal(),
        holdout_observations=4,
        baseline_consistent=True,
        candidate_consistent=False,
    )
    assert validation.canary_eligible is False
    assert validation.reason == "validation_consistency_failed"


def test_policy_lifecycle_requires_two_holdout_observations():
    validation = DirectorDecisionPolicyLifecycle.validate(
        _proposal(),
        holdout_observations=1,
        baseline_consistent=True,
        candidate_consistent=True,
    )
    assert validation.validation_id == ""
    assert validation.canary_eligible is False


def test_policy_lifecycle_canary_then_explicit_activation():
    proposal = _proposal()
    validation = DirectorDecisionPolicyLifecycle.validate(
        proposal,
        holdout_observations=3,
        baseline_consistent=True,
        candidate_consistent=True,
    )
    canary = DirectorDecisionPolicyLifecycle.enter_canary(
        proposal,
        validation,
        authorized_by="director-governance",
    )
    assert canary.state == "canary"

    with pytest.raises(DirectorDecisionPolicyLifecycleError, match="explicit policy authorization"):
        DirectorDecisionPolicyLifecycle.activate(
            canary,
            authorization_granted=False,
        )

    active = DirectorDecisionPolicyLifecycle.activate(
        canary,
        authorization_granted=True,
    )
    assert active.state == "active"


def test_policy_lifecycle_cannot_skip_canary():
    proposal = _proposal()
    validation = DirectorDecisionPolicyLifecycle.validate(
        proposal,
        holdout_observations=3,
        baseline_consistent=True,
        candidate_consistent=True,
    )
    fake_active = DirectorDecisionPolicyLifecycle.enter_canary(
        proposal,
        validation,
        authorized_by="director-governance",
    )
    object.__setattr__(fake_active, "state", "active")
    with pytest.raises(DirectorDecisionPolicyLifecycleError):
        DirectorDecisionPolicyLifecycle.activate(
            DirectorDecisionPolicyLifecycleActivationShim.from_activation(fake_active),
            authorization_granted=True,
        )


class DirectorDecisionPolicyLifecycleActivationShim:
    """Small test-only helper to keep the state machine test explicit."""

    @staticmethod
    def from_activation(value):
        from aurora.orchestration.director_decision_policy_lifecycle import (
            DirectorDecisionPolicyActivation,
        )

        return DirectorDecisionPolicyActivation(
            proposal_id=value.proposal_id,
            validation_id=value.validation_id,
            state="active",
            authorized_by=value.authorized_by,
            created_at=value.created_at,
        )

def test_policy_lifecycle_canary_can_be_rolled_back():
    proposal = _proposal()
    validation = DirectorDecisionPolicyLifecycle.validate(
        proposal,
        holdout_observations=3,
        baseline_consistent=True,
        candidate_consistent=True,
    )
    canary = DirectorDecisionPolicyLifecycle.enter_canary(
        proposal,
        validation,
        authorized_by="director-governance",
    )

    rolled_back = DirectorDecisionPolicyLifecycle.rollback(
        canary,
        reason="shadow integrity violation",
        authorized_by="shadow-canary-guard",
    )

    assert rolled_back.state == "rolled_back"
    assert rolled_back.proposal_id == canary.proposal_id
    assert rolled_back.validation_id == canary.validation_id

    from aurora.orchestration.director_decision_policy_canary import (
        DirectorDecisionPolicyCanaryError,
        DirectorDecisionPolicyShadowCanary,
    )

    with pytest.raises(DirectorDecisionPolicyCanaryError, match="canary state"):

        DirectorDecisionPolicyShadowCanary.observe(
            rolled_back,
            decision_event_id=101,
            baseline_decision="continue",
            candidate_decision="complete",
            actual_outcome="mission_succeeded",
        )
