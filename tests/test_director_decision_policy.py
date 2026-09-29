from aurora.orchestration.director_decision_performance import DirectorDecisionPerformance
from aurora.orchestration.director_decision_policy import DirectorDecisionPolicy


def test_policy_guidance_preserves_observed_decision_outcomes():
    performance = (
        DirectorDecisionPerformance(
            decision="continue",
            evaluations=4,
            average_score=0.75,
            successful_evaluations=3,
            success_rate=0.75,
        ),
        DirectorDecisionPerformance(
            decision="complete",
            evaluations=2,
            average_score=1.0,
            successful_evaluations=2,
            success_rate=1.0,
        ),
    )

    guidance = DirectorDecisionPolicy.guidance(performance)

    assert guidance[0].decision == "continue"
    assert guidance[0].evaluations == 4
    assert guidance[0].success_rate == 0.75
    assert guidance[1].decision == "complete"

    prompt = DirectorDecisionPolicy.prompt(guidance)

    assert "advisory" in prompt
    assert "continue: evaluations=4" in prompt
    assert "success_rate=0.750" in prompt
    assert "complete: evaluations=2" in prompt
