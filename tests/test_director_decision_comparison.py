from aurora.orchestration.director_decision_comparison import (
    DirectorDecisionComparativeEvidence,
    DirectorDecisionComparisonEvaluator,
)
from aurora.orchestration.director_decision_comparison_store import (
    DirectorDecisionComparisonStore,
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


def test_real_independent_comparative_evidence_separates_difference_direction_and_causality():
    evidence = DirectorDecisionComparativeEvidence(
        source="independent_execution",
        alternative_execution_id="alt-42",
        chosen_execution_id="chosen-41",
        chosen_outcome="mission_succeeded",
        alternative_outcome="mission_succeeded_with_lower_cost",
        comparability_basis=("same_objective", "same_environment_class", "same_time_window"),
        observed_direction="better",
    )
    result = DirectorDecisionComparisonEvaluator.evaluate(
        decision_event_id=14,
        chosen_decision="continue",
        alternative="complete",
        chosen_outcome="mission_succeeded",
        alternative_outcome="mission_succeeded_with_lower_cost",
        comparative_evidence=evidence,
    )
    assert result.state == "potentially_better_observed"
    assert result.observed_direction == "better"
    assert result.causal_attribution == "unsupported"
    assert "independent_execution" in result.evidence


def test_comparative_evidence_rejects_causal_attribution():
    try:
        DirectorDecisionComparativeEvidence(
            source="historical_comparable",
            alternative_execution_id="alt-1",
            chosen_execution_id="chosen-1",
            chosen_outcome="success",
            alternative_outcome="success",
            comparability_basis=("same_objective",),
            causal_attribution="causal",
        )
    except ValueError as exc:
        assert "causal attribution" in str(exc)
    else:
        raise AssertionError("causal attribution must remain unsupported")


def test_comparison_store_persists_observation_without_performance_aggregation(tmp_path):
    store = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    store.record(
        decision_event_id=15,
        chosen_decision="continue",
        alternative="complete",
        source="historical_comparable",
        alternative_execution_id="alt-h1",
        chosen_execution_id="chosen-h1",
        chosen_outcome="success",
        alternative_outcome="success_with_lower_cost",
        comparability_basis=("same_objective", "same_environment_class"),
        observed_direction="better",
        causal_attribution="unsupported",
        state="potentially_better_observed",
        reason="comparable_observed_difference_with_no_causal_attribution",
        evidence=("historical_comparable", "same_objective"),
        created_at="2026-09-29T15:00:00+00:00",
    )
    rows = store.list_for_decision(15)
    assert len(rows) == 1
    assert rows[0].state == "potentially_better_observed"
    assert rows[0].causal_attribution == "unsupported"
    assert rows[0].observed_direction == "better"
