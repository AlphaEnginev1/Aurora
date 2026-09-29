from aurora.orchestration.director_decision_policy_canary import (
    DirectorDecisionPolicyShadowCanary,
)
from aurora.orchestration.director_decision_policy_canary_evaluation import (
    DirectorDecisionPolicyCanaryEvaluator,
)
from aurora.orchestration.director_decision_policy_canary_evaluation_store import (
    DirectorDecisionPolicyCanaryEvaluationStore,
)
from aurora.orchestration.director_decision_policy_canary_store import (
    DirectorDecisionPolicyShadowCanaryStore,
)
from aurora.orchestration.director_decision_policy_evolution import DirectorDecisionPolicyEvolution
from aurora.orchestration.director_decision_policy_lifecycle import (
    DirectorDecisionPolicyLifecycle,
)
from aurora.orchestration.director_decision_knowledge import DirectorDecisionKnowledge


def _proposal():
    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-canary-evaluation",
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
        proposed_change="Test the complete decision in this recurring comparable context.",
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


def test_canary_evaluator_reports_governance_review_readiness(tmp_path):
    store = DirectorDecisionPolicyShadowCanaryStore(tmp_path / "runtime.sqlite")
    canary = _canary()

    for event_id, candidate in ((51, "continue"), (52, "complete")):
        store.record(
            DirectorDecisionPolicyShadowCanary.observe(
                canary,
                decision_event_id=event_id,
                baseline_decision="continue",
                candidate_decision=candidate,
                actual_outcome="mission_succeeded",
            )
        )

    evaluation = DirectorDecisionPolicyCanaryEvaluator.evaluate(
        store,
        canary.proposal_id,
        min_observations=2,
        created_at="2026-09-29T19:04:00+00:00",
    )

    assert evaluation.observations == 2
    assert evaluation.agreements == 1
    assert evaluation.disagreements == 1
    assert evaluation.agreement_rate == 0.5
    assert evaluation.governance_review_ready is True
    assert evaluation.rollback_required is False
    assert evaluation.state == "governance_review"
    assert evaluation.reason == "shadow_evidence_ready_for_governance_review"


def test_canary_evaluator_stays_non_ready_below_threshold(tmp_path):
    store = DirectorDecisionPolicyShadowCanaryStore(tmp_path / "runtime.sqlite")
    canary = _canary()

    store.record(
        DirectorDecisionPolicyShadowCanary.observe(
            canary,
            decision_event_id=61,
            baseline_decision="continue",
            candidate_decision="continue",
            actual_outcome="mission_succeeded",
        )
    )

    evaluation = DirectorDecisionPolicyCanaryEvaluator.evaluate(
        store,
        canary.proposal_id,
        min_observations=2,
    )

    assert evaluation.governance_review_ready is False
    assert evaluation.rollback_required is False
    assert evaluation.reason == "insufficient_shadow_observations"


def test_canary_evaluation_store_persists_latest(tmp_path):
    shadow_store = DirectorDecisionPolicyShadowCanaryStore(tmp_path / "runtime.sqlite")
    evaluation_store = DirectorDecisionPolicyCanaryEvaluationStore(tmp_path / "runtime.sqlite")
    canary = _canary()

    evaluation = DirectorDecisionPolicyCanaryEvaluator.evaluate(
        shadow_store,
        canary.proposal_id,
        min_observations=1,
    )
    evaluation_store.record(evaluation)

    loaded = evaluation_store.get(evaluation.evaluation_id)
    latest = evaluation_store.latest_for_proposal(canary.proposal_id)

    assert loaded == evaluation
    assert latest == evaluation
