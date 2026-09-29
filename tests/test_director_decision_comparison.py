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


def test_comparison_store_deduplicates_and_summarizes_repeated_observations(tmp_path):
    store = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    common = {
        "decision_event_id": 16,
        "chosen_decision": "continue",
        "alternative": "complete",
        "source": "independent_execution",
        "chosen_outcome": "mission_succeeded",
        "alternative_outcome": "mission_succeeded_with_lower_cost",
        "comparability_basis": ("same_objective", "same_environment_class"),
        "observed_direction": "better",
        "causal_attribution": "unsupported",
        "state": "potentially_better_observed",
        "reason": "comparable_observed_difference_with_no_causal_attribution",
        "evidence": ("independent_execution", "same_objective"),
        "created_at": "2026-09-29T15:10:00+00:00",
    }
    store.record(**common, alternative_execution_id="alt-1", chosen_execution_id="chosen-1")
    store.record(**common, alternative_execution_id="alt-1", chosen_execution_id="chosen-1")
    second = {**common, "created_at": "2026-09-29T15:11:00+00:00"}
    store.record(
        **second,
        alternative_execution_id="alt-2",
        chosen_execution_id="chosen-2",
    )
    rows = store.list_for_decision(16)
    assert len(rows) == 2
    summary = store.summarize_for_decision(16)
    assert len(summary) == 1
    assert summary[0].observations == 2
    assert summary[0].better_observations == 2
    assert summary[0].worse_observations == 0
    assert summary[0].consistent_direction == "better"
    assert summary[0].sources == ("independent_execution",)


def test_comparison_summary_marks_mixed_direction_without_selecting_a_winner(tmp_path):
    store = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    for index, direction in enumerate(("better", "worse"), start=1):
        store.record(
            decision_event_id=17,
            chosen_decision="continue",
            alternative="complete",
            source="historical_comparable",
            alternative_execution_id=f"alt-{index}",
            chosen_execution_id=f"chosen-{index}",
            chosen_outcome="observed",
            alternative_outcome="observed",
            comparability_basis=("same_objective",),
            observed_direction=direction,
            causal_attribution="unsupported",
            state="observational_difference",
            reason="comparable_observed_difference_with_no_causal_attribution",
            evidence=("historical_comparable",),
            created_at=f"2026-09-29T15:1{index}:00+00:00",
        )
    summary = store.summarize_for_decision(17)
    assert len(summary) == 1
    assert summary[0].observations == 2
    assert summary[0].better_observations == 1
    assert summary[0].worse_observations == 1
    assert summary[0].consistent_direction == "mixed"


def test_comparison_store_replication_across_distinct_decisions_requires_same_comparability_basis(tmp_path):
    store = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    for decision_event_id in (18, 19):
        store.record(
            decision_event_id=decision_event_id,
            chosen_decision="continue",
            alternative="complete",
            source="independent_execution",
            alternative_execution_id=f"alt-{decision_event_id}",
            chosen_execution_id=f"chosen-{decision_event_id}",
            chosen_outcome="mission_succeeded",
            alternative_outcome="mission_succeeded_with_lower_cost",
            comparability_basis=("same_objective", "same_environment_class"),
            observed_direction="better",
            causal_attribution="unsupported",
            state="potentially_better_observed",
            reason="comparable_observed_difference_with_no_causal_attribution",
            evidence=("independent_execution",),
            created_at=f"2026-09-29T16:{decision_event_id}:00+00:00",
        )
    store.record(
        decision_event_id=20,
        chosen_decision="continue",
        alternative="complete",
        source="independent_execution",
        alternative_execution_id="alt-20",
        chosen_execution_id="chosen-20",
        chosen_outcome="mission_succeeded",
        alternative_outcome="mission_succeeded_with_lower_cost",
        comparability_basis=("same_objective", "different_environment"),
        observed_direction="better",
        causal_attribution="unsupported",
        state="potentially_better_observed",
        reason="comparable_observed_difference_with_no_causal_attribution",
        evidence=("independent_execution",),
        created_at="2026-09-29T16:20:00+00:00",
    )
    summaries = store.summarize_across_decisions(
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
    )
    assert len(summaries) == 1
    assert summaries[0].decision_events == 2
    assert summaries[0].observations == 2
    assert summaries[0].better_observations == 2
    assert summaries[0].consistent_direction == "better"
    assert summaries[0].sources == ("independent_execution",)
