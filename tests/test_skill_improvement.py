from pathlib import Path

from aurora.inference.models import InferenceResponse
from aurora.skills.evaluation import SkillEvaluation
from aurora.skills.improvement import SkillImprovementGenerator
from aurora.skills.learning import SkillEvolutionProposal, SkillLearningStore
from aurora.skills.models import SkillSpec


class FakeRouter:
    def generate(self, request, preferred_models=()):
        return InferenceResponse(
            model="improvement-model",
            provider="test",
            content="RATIONALE:\nObserved failures require clearer source verification.\n"
                    "INSTRUCTIONS:\nCross-check independent sources before completion.",
        )


def evaluation() -> SkillEvaluation:
    return SkillEvaluation(
        skill_id="research", skill_version="1.0.0", mission_id="m", task_id="t",
        role="researcher", score=0.4, status="failed", model="old-model",
        verification_passed=(), verification_failed=("source",),
        verification_missing=(), quality_passed=(), quality_failed=("accuracy",),
        quality_missing=(), failure_signature="weak evidence",
        evidence_completeness=1.0, evidence_state="evidence_complete",
    )


def test_generator_uses_durable_evidence_and_returns_candidate_procedure(tmp_path: Path):
    store = SkillLearningStore(tmp_path / "learning.db")
    for _ in range(3):
        store.record(evaluation())
    proposal = SkillEvolutionProposal(
        proposal_id="p", skill_id="research", base_version="1.0.0",
        candidate_version="1.0.1", reason="low score across 3 evaluations",
        evidence_count=3, suggested_change="Improve evidence verification.",
    )
    skill = SkillSpec(
        skill_id="research", version="1.0.0", name="Research",
        mission="Research evidence", instructions="Collect sources.",
        required_capabilities=frozenset(), verification_requirements=("source",),
        quality_checks=("accuracy",), tags=("research",),
    )
    result = SkillImprovementGenerator(FakeRouter(), store).generate(
        active=skill, proposal=proposal,
    )
    assert result.model == "improvement-model"
    assert "Cross-check independent sources" in result.instructions


def test_generator_rejects_missing_instruction_section(tmp_path: Path):
    class BadRouter:
        def generate(self, request, preferred_models=()):
            return InferenceResponse(model="bad", provider="test", content="RATIONALE: nope")

    store = SkillLearningStore(tmp_path / "learning.db")
    store.record(evaluation())
    proposal = SkillEvolutionProposal(
        proposal_id="p", skill_id="research", base_version="1.0.0",
        candidate_version="1.0.1", reason="failure", evidence_count=1,
        suggested_change="Improve.",
    )
    skill = SkillSpec(
        skill_id="research", version="1.0.0", name="Research", mission="Research",
        instructions="Collect.", required_capabilities=frozenset(),
        verification_requirements=("source",), quality_checks=("accuracy",),
    )
    try:
        SkillImprovementGenerator(BadRouter(), store).generate(active=skill, proposal=proposal)
    except RuntimeError as exc:
        assert "INSTRUCTIONS" in str(exc)
    else:
        raise AssertionError("unsafe model output was accepted")
