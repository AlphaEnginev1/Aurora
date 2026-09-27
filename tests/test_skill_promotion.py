from aurora.skills.acquisition import SkillAcquisitionRuntime
from aurora.skills.acquisition_pipeline import SkillAcquisitionPipeline
from aurora.skills.models import SkillSpec
from aurora.skills.registry import SkillRegistry

def test_approved_skill_promotes_and_advances_registry(tmp_path):
    runtime=SkillAcquisitionRuntime(tmp_path/"acquisition.db")
    pipeline=SkillAcquisitionPipeline(runtime,tmp_path/"artifacts.db")
    gap=runtime.detect_gap(
        mission_id="m-promote",skill_id="specialist",reason="missing specialist",
        required_capabilities=("research",),
    )
    skill=SkillSpec(
        "specialist","1.0.0","Specialist","specialist","perform work",
        frozenset({"research"}),("source_traceability",),("reproducibility",),
        tags=("specialist",),
    )
    registry=SkillRegistry({})
    prepared=pipeline.prepare_candidate(
        gap=gap,skill=skill,registry=registry,source_ids=("src-1",),
        acquired_tools=(),validation_returncode=0,validation_stdout="validated",
        construction_score=0.9,construction_evidence_state="complete",
    )
    approved=pipeline.approve(prepared)
    promoted=pipeline.promote(type(prepared)(approved,prepared.evidence),registry)
    assert promoted.skills["specialist"].version=="1.0.0"
