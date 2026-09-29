from pathlib import Path

from aurora.orchestration.director_decision_performance import (
    DirectorDecisionPerformanceStore,
)


def test_director_decision_performance_aggregates_only_complete_evidence(tmp_path: Path):
    store = DirectorDecisionPerformanceStore(tmp_path / "tasks.db")

    store.record(
        decision_event_id=10,
        round=1,
        decision="continue",
        score=1.0,
        outcome="follow_up_succeeded",
        evidence=("director_decision_executed", "mission_reopened", "task_completed", "mission_succeeded"),
        created_at="2026-09-29T07:00:00+00:00",
    )
    store.record(
        decision_event_id=11,
        round=2,
        decision="continue",
        score=0.0,
        outcome="follow_up_failed",
        evidence=("director_decision_executed", "mission_reopened", "task_failed", "mission_failed"),
        created_at="2026-09-29T07:01:00+00:00",
    )
    store.record(
        decision_event_id=12,
        round=2,
        decision="complete",
        score=1.0,
        outcome="mission_completed",
        evidence=("director_decision_executed", "mission_succeeded"),
        created_at="2026-09-29T07:02:00+00:00",
    )
    store.record(
        decision_event_id=13,
        round=3,
        decision="complete",
        score=None,
        outcome="mission_completed",
        evidence=("mission_succeeded",),
        evidence_completeness="partial",
        sufficiency_reasons=("execution_evidence_missing",),
        created_at="2026-09-29T07:03:00+00:00",
    )
    store.record(
        decision_event_id=10,
        round=1,
        decision="continue",
        score=0.0,
        outcome="duplicate",
        evidence=(),
        created_at="2026-09-29T07:04:00+00:00",
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
            evaluations=3,
            average_score=0.6666666666666666,
            successful_evaluations=2,
            success_rate=2 / 3,
        ),
    )
