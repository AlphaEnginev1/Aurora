import pytest

from aurora.orchestration.director_decision_policy_active_store import (
    DirectorDecisionPolicyActiveStore,
)
from aurora.orchestration.director_decision_policy_activation_controller import (
    DirectorDecisionPolicyActivationController,
)
from aurora.orchestration.director_decision_policy_canary import (
    DirectorDecisionPolicyShadowCanary,
)
from aurora.orchestration.director_decision_policy_canary_evaluation import (
    DirectorDecisionPolicyCanaryEvaluator,
)
from aurora.orchestration.director_decision_policy_canary_store import (
    DirectorDecisionPolicyShadowCanaryStore,
)
from aurora.orchestration.director_decision_policy_evolution import DirectorDecisionPolicyEvolution
from aurora.orchestration.director_decision_policy_lifecycle import (
    DirectorDecisionPolicyLifecycle,
    DirectorDecisionPolicyLifecycleError,
)
from aurora.orchestration.director_decision_knowledge import DirectorDecisionKnowledge


def _canary():
    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-activation-controller",
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
    proposal = DirectorDecisionPolicyEvolution.propose(
        knowledge,
        proposed_change="Test complete in the recurring comparable context.",
        created_at="2026-09-29T19:01:00+00:00",
    )
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


def test_activation_controller_requires_governance_ready_shadow(tmp_path):
    shadow = DirectorDecisionPolicyShadowCanaryStore(tmp_path / "runtime.sqlite")
    evaluation = DirectorDecisionPolicyCanaryEvaluator.evaluate(
        shadow,
        _canary().proposal_id,
        min_observations=2,
    )
    controller = DirectorDecisionPolicyActivationController(
        DirectorDecisionPolicyActiveStore(tmp_path / "runtime.sqlite")
    )

    with pytest.raises(DirectorDecisionPolicyLifecycleError, match="not ready"):
        controller.activate(
            _canary(),
            evaluation,
            authorization_granted=True,
        )


def test_activation_controller_promotes_only_with_explicit_authorization(tmp_path):
    shadow = DirectorDecisionPolicyShadowCanaryStore(tmp_path / "runtime.sqlite")
    canary = _canary()
    for event_id, candidate in ((71, "continue"), (72, "complete")):
        shadow.record(
            DirectorDecisionPolicyShadowCanary.observe(
                canary,
                decision_event_id=event_id,
                baseline_decision="continue",
                candidate_decision=candidate,
                actual_outcome="mission_succeeded",
            )
        )
    evaluation = DirectorDecisionPolicyCanaryEvaluator.evaluate(
        shadow,
        canary.proposal_id,
        min_observations=2,
    )
    active_store = DirectorDecisionPolicyActiveStore(tmp_path / "runtime.sqlite")
    controller = DirectorDecisionPolicyActivationController(active_store)

    with pytest.raises(DirectorDecisionPolicyLifecycleError, match="explicit policy authorization"):
        controller.activate(
            canary,
            evaluation,
            authorization_granted=False,
        )

    active = controller.activate(
        canary,
        evaluation,
        authorization_granted=True,
        created_at="2026-09-29T19:05:00+00:00",
    )

    assert active.state == "active"
    assert active_store.current() is not None
    assert active_store.current().proposal_id == canary.proposal_id


def test_activation_controller_rolls_back_active_policy_and_removes_current(tmp_path):
    shadow = DirectorDecisionPolicyShadowCanaryStore(tmp_path / "runtime.sqlite")
    canary = _canary()
    shadow.record(
        DirectorDecisionPolicyShadowCanary.observe(
            canary,
            decision_event_id=81,
            baseline_decision="continue",
            candidate_decision="continue",
            actual_outcome="mission_succeeded",
        )
    )
    shadow.record(
        DirectorDecisionPolicyShadowCanary.observe(
            canary,
            decision_event_id=82,
            baseline_decision="continue",
            candidate_decision="continue",
            actual_outcome="mission_succeeded",
        )
    )
    evaluation = DirectorDecisionPolicyCanaryEvaluator.evaluate(
        shadow,
        canary.proposal_id,
        min_observations=2,
    )
    active_store = DirectorDecisionPolicyActiveStore(tmp_path / "runtime.sqlite")
    controller = DirectorDecisionPolicyActivationController(active_store)
    active = controller.activate(canary, evaluation, authorization_granted=True)

    rolled_back = controller.rollback_active(
        active,
        reason="verified regression",
        authorized_by="director-governance",
    )

    assert rolled_back.state == "rolled_back"
    assert active_store.current() is None
    assert len(active_store.history()) == 1
