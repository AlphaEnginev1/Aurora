import pytest

from aurora.orchestration.director_decision_policy_canary import (
    DirectorDecisionPolicyCanaryError,
    DirectorDecisionPolicyShadowCanary,
)
from aurora.orchestration.director_decision_policy_canary_store import (
    DirectorDecisionPolicyShadowCanaryStore,
)
from aurora.orchestration.director_decision_policy_lifecycle import (
    DirectorDecisionPolicyLifecycle,
)
from aurora.orchestration.director_decision_policy_evolution import DirectorDecisionPolicyEvolution
from aurora.orchestration.director_decision_knowledge import DirectorDecisionKnowledge


def _proposal():
    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-canary",
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


def _canary():
    proposal = _proposal()
    validation = DirectorDecisionPolicyLifecycle.validate(
        proposal,
        holdout_observations=4,
        baseline_consistent=True,
        candidate_consistent=True,
        created_at="2026-09-29T19:02:00+00:00",
    )
    return DirectorDecisionPolicyLifecycle.enter_canary(
        proposal,
        validation,
        authorized_by="director-governance",
        created_at="2026-09-29T19:03:00+00:00",
    )


def test_shadow_canary_records_candidate_without_execution():
    observation = DirectorDecisionPolicyShadowCanary.observe(
        _canary(),
        decision_event_id=21,
        baseline_decision="continue",
        candidate_decision="complete",
        actual_outcome="mission_succeeded",
        created_at="2026-09-29T19:04:00+00:00",
    )

    assert observation.decision_agreement is False
    assert observation.baseline_executed is True
    assert observation.candidate_executed is False
    assert observation.state == "shadow_observed"
    assert DirectorDecisionPolicyShadowCanary.boundary(observation) == "candidate_is_shadow_only"


def test_shadow_canary_rejects_non_canary_activation():
    proposal = _proposal()
    validation = DirectorDecisionPolicyLifecycle.validate(
        proposal,
        holdout_observations=4,
        baseline_consistent=True,
        candidate_consistent=True,
    )
    active = DirectorDecisionPolicyLifecycle.activate(
        DirectorDecisionPolicyLifecycle.enter_canary(
            proposal,
            validation,
            authorized_by="director-governance",
        ),
        authorization_granted=True,
    )

    with pytest.raises(DirectorDecisionPolicyCanaryError, match="canary state"):
        DirectorDecisionPolicyShadowCanary.observe(
            active,
            decision_event_id=22,
            baseline_decision="continue",
            candidate_decision="complete",
            actual_outcome="mission_succeeded",
        )


def test_shadow_canary_rejects_candidate_execution():
    canary = _canary()
    observation = DirectorDecisionPolicyShadowCanary.observe(
        canary,
        decision_event_id=23,
        baseline_decision="continue",
        candidate_decision="continue",
        actual_outcome="mission_succeeded",
    )
    object.__setattr__(observation, "candidate_executed", True)

    with pytest.raises(DirectorDecisionPolicyCanaryError, match="candidate execution"):
        DirectorDecisionPolicyShadowCanary.boundary(observation)


def test_shadow_canary_store_dedupes_and_summarizes(tmp_path):
    store = DirectorDecisionPolicyShadowCanaryStore(tmp_path / "runtime.sqlite")
    canary = _canary()

    first = DirectorDecisionPolicyShadowCanary.observe(
        canary,
        decision_event_id=31,
        baseline_decision="continue",
        candidate_decision="complete",
        actual_outcome="mission_succeeded",
        created_at="2026-09-29T19:05:00+00:00",
    )
    second = DirectorDecisionPolicyShadowCanary.observe(
        canary,
        decision_event_id=32,
        baseline_decision="continue",
        candidate_decision="continue",
        actual_outcome="mission_succeeded",
        created_at="2026-09-29T19:06:00+00:00",
    )

    store.record(first)
    store.record(first)
    store.record(second)

    rows = store.list_for_proposal(canary.proposal_id)
    summary = store.summarize_for_proposal(canary.proposal_id)

    assert len(rows) == 2
    assert summary.observations == 2
    assert summary.agreements == 1
    assert summary.disagreements == 1
    assert summary.outcomes_observed == 2
    assert summary.candidate_execution_attempts == 0
    assert summary.state == "shadow_integrity_intact"
    assert summary.baseline_outcomes == ("mission_succeeded",)
