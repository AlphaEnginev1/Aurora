from pathlib import Path

from aurora.orchestration.director_decision_performance import (
    DirectorDecisionPerformanceStore,
)


def test_director_decision_performance_aggregates_durable_outcomes(tmp_path: Path):
    store = DirectorDecisionPerformanceStore(tmp_path / "tasks.db")

    store.record(
        decision_event_id=10,
        round=1,
        decision="continue",
        score=1.0,
        outcome="follow_up_succeeded",
        evidence=("mission_reopened", "mission_succeeded"),
        created_at="2026-09-29T07:00:00+00:00",
    )
    store.record(
        decision_event_id=11,
        round=2,
        decision="continue",
        score=0.0,
        outcome="follow_up_failed",
        evidence=("mission_reopened", "mission_failed"),
        created_at="2026-09-29T07:01:00+00:00",
    )
    store.record(
        decision_event_id=12,
        round=2,
        decision="complete",
        score=1.0,
        outcome="mission_completed",
        evidence=("mission_succeeded",),
        created_at="2026-09-29T07:02:00+00:00",
    )
    store.record(
        decision_event_id=10,
        round=1,
        decision="continue",
        score=0.0,
        outcome="duplicate",
        evidence=(),
        created_at="2026-09-29T07:03:00+00:00",
    )

    summary = store.summary()

    assert summary == (
        type(summary[0])(
            decision="complete",
            evaluations=1,
            average_score=1.0,
            successful_evaluations=1,
            success_rate=1.0,
        ),
        type(summary[1])(
            decision="continue",
            evaluations=2,
            average_score=0.5,
            successful_evaluations=1,
            success_rate=0.5,
        ),
    )
