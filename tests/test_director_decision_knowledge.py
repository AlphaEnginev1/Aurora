from pathlib import Path

import pytest

from aurora.orchestration.director_decision_comparison_store import (
    DirectorDecisionComparisonStore,
)
from aurora.orchestration.director_decision_knowledge import (
    DirectorDecisionKnowledgeError,
    DirectorDecisionKnowledgePromoter,
)
from aurora.orchestration.director_decision_knowledge_store import (
    DirectorDecisionKnowledgeStore,
)


def _record(
    store: DirectorDecisionComparisonStore,
    *,
    decision_event_id: int,
    execution_suffix: str,
    direction: str = "better",
    basis: tuple[str, ...] = ("same_objective", "same_environment_class"),
):
    store.record(
        decision_event_id=decision_event_id,
        chosen_decision="continue",
        alternative="complete",
        source="independent_execution",
        alternative_execution_id=f"alternative-{execution_suffix}",
        chosen_execution_id=f"chosen-{execution_suffix}",
        chosen_outcome="follow_up_succeeded",
        alternative_outcome="mission_succeeded",
        comparability_basis=basis,
        observed_direction=direction,
        causal_attribution="unsupported",
        state="potentially_better_observed",
        reason="comparable_observed_difference_with_no_causal_attribution",
        evidence=("independent_execution",),
        created_at=f"2026-09-29T17:{decision_event_id:02d}:00+00:00",
    )


def test_promotes_replicated_consistent_comparative_knowledge(tmp_path: Path):
    store = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    _record(store, decision_event_id=21, execution_suffix="a")
    _record(store, decision_event_id=22, execution_suffix="b")

    summary = store.summarize_across_decisions(
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
    )
    assert summary[0].decision_events == 2
    assert summary[0].observations == 2

    promotion = DirectorDecisionKnowledgePromoter(store).promote(summary[0])

    assert promotion.promoted is True
    assert promotion.reason == "replicated_consistent_observation"
    assert promotion.knowledge is not None
    assert promotion.knowledge.state == "promoted_empirical"
    assert promotion.knowledge.observed_direction == "better"
    assert "does not establish causality" in promotion.knowledge.claim


def test_promotion_rejects_mixed_direction(tmp_path: Path):
    store = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    _record(store, decision_event_id=23, execution_suffix="a", direction="better")
    _record(store, decision_event_id=24, execution_suffix="b", direction="worse")

    summary = store.summarize_across_decisions(
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
    )[0]
    promotion = DirectorDecisionKnowledgePromoter(store).promote(summary)

    assert promotion.promoted is False
    assert promotion.reason == "direction_not_consistent"


def test_promotion_fails_closed_on_tampered_causal_attribution(tmp_path: Path):
    store = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    _record(store, decision_event_id=25, execution_suffix="a")
    _record(store, decision_event_id=26, execution_suffix="b")
    summary = store.summarize_across_decisions(
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
    )[0]

    with store._connect() as db:
        db.execute(
            """
            UPDATE director_decision_comparisons
            SET causal_attribution = 'causal'
            WHERE decision_event_id = 26
            """
        )

    with pytest.raises(DirectorDecisionKnowledgeError, match="causal attribution"):
        DirectorDecisionKnowledgePromoter(store).promote(summary)


def test_promotion_requires_distinct_decision_events(tmp_path: Path):
    store = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    _record(store, decision_event_id=29, execution_suffix="a")
    _record(
        store,
        decision_event_id=29,
        execution_suffix="b",
    )

    summary = store.summarize_across_decisions(
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
    )[0]
    assert summary.decision_events == 1

    promotion = DirectorDecisionKnowledgePromoter(store).promote(summary)
    assert promotion.promoted is False
    assert promotion.reason == "insufficient_decision_replication"


def test_knowledge_store_deduplicates_promoted_cohort(tmp_path: Path):
    comparisons = DirectorDecisionComparisonStore(tmp_path / "tasks.db")
    _record(comparisons, decision_event_id=30, execution_suffix="a")
    _record(comparisons, decision_event_id=31, execution_suffix="b")
    summary = comparisons.summarize_across_decisions(
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
    )[0]
    knowledge = DirectorDecisionKnowledgePromoter(comparisons).promote(summary).knowledge
    assert knowledge is not None

    store = DirectorDecisionKnowledgeStore(tmp_path / "tasks.db")
    store.record(knowledge)
    store.record(knowledge)

    rows = store.list_all()
    assert len(rows) == 1
    assert store.get(knowledge.knowledge_id) == knowledge
