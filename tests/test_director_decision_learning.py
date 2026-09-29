from aurora.orchestration.director_decision_learning import DirectorDecisionLearning
from aurora.orchestration.task_store import TaskEvent


def event(event_id: int, decision: str, round_number: int = 1) -> TaskEvent:
    return TaskEvent(
        event_id=event_id,
        mission_id="mission-1",
        task_id=None,
        event_type="director_review",
        payload={"decision": decision, "round": round_number},
        created_at="2026-09-29T07:00:00+00:00",
    )


def test_successful_decision_is_not_claimed_as_optimal():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(10, "complete"),
        observed_event_types=("mission_succeeded",),
    )

    assert evaluation is not None
    assert evaluation.score == 1.0
    assert evaluation.quality == "supported_success"
    assert evaluation.evidence_state == "outcome_observed"
    assert evaluation.attribution == "temporal_outcome_alignment"


def test_failed_follow_up_requires_correction():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(11, "continue"),
        observed_event_types=("mission_reopened", "mission_failed"),
    )

    assert evaluation is not None
    assert evaluation.score == 0.0
    assert evaluation.outcome == "follow_up_failed"
    assert evaluation.quality == "correction_required"


def test_blocked_decision_has_explicit_quality():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(12, "blocked"),
        observed_event_types=("mission_blocked",),
    )

    assert evaluation is not None
    assert evaluation.quality == "supported_block"
    assert evaluation.evidence_state == "outcome_observed"
