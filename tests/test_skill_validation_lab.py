from pathlib import Path

from aurora.inference.models import InferenceResponse
from aurora.skills.learning import SkillLearningStore
from aurora.skills.models import SkillSpec
from aurora.skills.validation_lab import SkillValidationCase, SkillValidationError, SkillValidationLab


class FakeRouter:
    def generate(self, request, preferred_models=()):
        prompt = request.messages[-1]["content"]
        candidate = "independent source cross-checking" in prompt
        state = "pass" if candidate else "fail"
        return InferenceResponse(
            model="validation-model",
            provider="test",
            content=f"VERIFY:source={state}\nQUALITY:accuracy={state}",
        )


def skills():
    baseline = SkillSpec(
        skill_id="research", version="1.0.0", name="Research",
        mission="Research evidence", instructions="Collect evidence.",
        required_capabilities=frozenset(), verification_requirements=("source",),
        quality_checks=("accuracy",),
    )
    candidate = SkillSpec(
        skill_id="research", version="1.0.1", name="Research",
        mission="Research evidence", instructions="Use independent source cross-checking.",
        required_capabilities=frozenset(), verification_requirements=("source",),
        quality_checks=("accuracy",),
    )
    return baseline, candidate


def test_validation_lab_requires_measurable_candidate_improvement():
    baseline, candidate = skills()
    report = SkillValidationLab(FakeRouter()).compare(
        baseline=baseline,
        candidate=candidate,
        cases=(SkillValidationCase("case-1", "verify evidence", ("source", "accuracy")),),
    )
    assert report.state == "improved"
    assert report.baseline_score < report.candidate_score
    assert report.improvement >= 0.03


def test_validation_lab_fails_closed_when_candidate_does_not_improve():
    baseline, _ = skills()
    try:
        SkillValidationLab(FakeRouter()).compare(
            baseline=baseline,
            candidate=baseline,
            cases=(SkillValidationCase("case-1", "verify evidence", ("source", "accuracy")),),
        )
    except SkillValidationError as exc:
        assert "did not demonstrate required improvement" in str(exc)
    else:
        raise AssertionError("non-improving skill was accepted")
