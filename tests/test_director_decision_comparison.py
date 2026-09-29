from aurora.orchestration.director_decision_comparison import (
    DirectorDecisionComparisonEvaluator,
)


def test_unexecuted_alternative_never_becomes_counterfactual_conclusion():
    result = DirectorDecisionComparisonEvaluator.evaluate(
        decision_event_id=10,
        chosen_decision="continue",
        alternative="complete",
        chosen_outcome="follow_up_succeeded",
        alternative_outcome=None,
        evidence=("chosen_outcome_observed",),
        alternative_executed=False,
    )

    assert result.state == "not_established"
    assert result.reason == "alternative_not_executed"


def test_non_comparable_alternative_is_not_used_as_counterfactual():
    result = DirectorDecisionComparisonEvaluator.evaluate(
        decision_event_id=11,
        chosen_decision="continue",
        alternative="complete",
        chosen_outcome="follow_up_succeeded",
        alternative_outcome="mission_completed",
        evidence=("both_outcomes_observed",),
        comparable=False,
        alternative_executed=True,
    )

    assert result.state == "not_established"
    assert result.reason == "evidence_not_comparable"


def test_different_observed_outcomes_do_not_claim_causality():
    result = DirectorDecisionComparisonEvaluator.evaluate(
        decision_event_id=12,
        chosen_decision="continue",
        alternative="complete",
        chosen_outcome="follow_up_succeeded",
        alternative_outcome="mission_failed",
        evidence=("chosen_executed", "alternative_executed", "outcomes_observed"),
        comparable=True,
        alternative_executed=True,
    )

    assert result.state == "observational_difference"
    assert result.reason == "different_observed_outcomes_without_causal_attribution"


def test_same_observed_outcome_has_no_comparative_difference():
    result = DirectorDecisionComparisonEvaluator.evaluate(
        decision_event_id=13,
        chosen_decision="continue",
        alternative="complete",
        chosen_outcome="follow_up_succeeded",
        alternative_outcome="follow_up_succeeded",
        evidence=("both_executed", "outcomes_observed"),
        comparable=True,
        alternative_executed=True,
    )

    assert result.state == "observational_difference"
    assert result.reason == "no_observed_outcome_difference"
