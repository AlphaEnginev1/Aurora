from pathlib import Path

from aurora.skills.acquisition import SkillAcquisitionError, SkillAcquisitionRuntime
from aurora.skills.models import SkillSpec
from aurora.skills.registry import SkillRegistry


def test_skill_gap_candidate_validation_governance_and_promotion(tmp_path):
    registry = SkillRegistry({})
    runtime = SkillAcquisitionRuntime(tmp_path / "acquisition.db")
    gap = runtime.detect_gap(
        mission_id="mission-1",
        skill_id="novel-research",
        reason="No authorized specialist currently covers the research method.",
        required_capabilities=("web",),
    )
    candidate_skill = SkillSpec(
        skill_id="novel-research",
        version="1.0.0",
        name="Novel Research",
        mission="investigate unfamiliar research methods",
        instructions="Follow evidence-first research procedure.",
        required_capabilities=frozenset({"web"}),
        verification_requirements=("source",),
        quality_checks=("traceability",),
        tags=("research",),
    )
    candidate = runtime.propose_candidate(gap=gap, skill=candidate_skill, registry=registry)
    validated = runtime.validate_candidate(candidate, registry)
    assert validated.status == "validated"

    approved = runtime.approve(validated)
    assert approved.status == "approved"
    promoted = runtime.promote(approved, registry)
    assert promoted.resolve("novel-research").version == "1.0.0"


def test_skill_candidate_cannot_be_promoted_without_governance(tmp_path):
    registry = SkillRegistry({})
    runtime = SkillAcquisitionRuntime(tmp_path / "acquisition.db")
    gap = runtime.detect_gap(mission_id="m2", skill_id="x", reason="missing")
    skill = SkillSpec(
        "x", "1.0.0", "X", "test", "procedure", frozenset(),
        ("check",), ("quality",)
    )
    candidate = runtime.propose_candidate(gap=gap, skill=skill, registry=registry)
    validated = runtime.validate_candidate(candidate, registry)
    try:
        runtime.promote(validated, registry)
    except SkillAcquisitionError as exc:
        assert "approved" in str(exc)
    else:
        raise AssertionError("unapproved skill candidate was promoted")
