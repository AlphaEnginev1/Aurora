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


def test_successful_decision_requires_complete_evidence():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(10, "complete"),
        observed_event_types=("director_decision_executed", "mission_succeeded"),
    )

    assert evaluation is not None
    assert evaluation.score == 1.0
    assert evaluation.quality == "supported_success"
    assert evaluation.evidence_state == "outcome_observed"
    assert evaluation.evidence_completeness == "complete"
    assert "result_observed" in evaluation.sufficiency_reasons
    assert "execution_observed" in evaluation.sufficiency_reasons
    assert evaluation.attribution == "temporal_outcome_alignment"


def test_successful_result_without_execution_is_only_partial_evidence():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(10, "complete"),
        observed_event_types=("mission_succeeded",),
    )

    assert evaluation is not None
    assert evaluation.score is None
    assert evaluation.quality == "evaluation_pending"
    assert evaluation.evidence_state == "evaluation_pending"
    assert evaluation.evidence_completeness == "partial"
    assert evaluation.attribution == "insufficient_evidence_for_quality_attribution"
    assert "execution_evidence_missing" in evaluation.sufficiency_reasons


def test_failed_follow_up_requires_complete_execution_and_task_evidence():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(11, "continue"),
        observed_event_types=(
            "director_decision_executed",
            "mission_reopened",
            "task_failed",
            "mission_failed",
        ),
    )

    assert evaluation is not None
    assert evaluation.score == 0.0
    assert evaluation.outcome == "follow_up_failed"
    assert evaluation.quality == "correction_required"
    assert evaluation.evidence_completeness == "complete"


def test_blocked_decision_has_explicit_quality():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(12, "blocked"),
        observed_event_types=("director_decision_executed", "mission_blocked"),
    )

    assert evaluation is not None
    assert evaluation.quality == "supported_block"
    assert evaluation.evidence_state == "outcome_observed"
    assert evaluation.evidence_completeness == "complete"


def test_continue_without_execution_or_task_evidence_is_partial():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(13, "continue"),
        observed_event_types=("mission_reopened", "mission_succeeded"),
    )

    assert evaluation is not None
    assert evaluation.score is None
    assert evaluation.evidence_completeness == "partial"
    assert evaluation.evidence_state == "evaluation_pending"


def test_insufficient_evidence_is_reserved_for_no_observed_result():
    evaluation = DirectorDecisionLearning.evaluate(
        decision_event=event(14, "complete"),
        observed_event_types=(),
    )

    assert evaluation is None
