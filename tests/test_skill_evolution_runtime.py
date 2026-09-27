from pathlib import Path

from aurora.skills.evolution_runtime import SkillEvolutionRuntime
from aurora.skills.learning import SkillEvolutionEngine, SkillLearningStore
from aurora.skills.models import SkillSpec
from aurora.skills.registry import SkillRegistry
from aurora.skills.evaluation import SkillEvaluation
from aurora.skills.validation_lab import SkillValidationLab
from aurora.inference.models import InferenceResponse


def make_registry() -> SkillRegistry:
    return SkillRegistry({
        "research": SkillSpec(
            skill_id="research",
            version="1.0.0",
            name="Research",
            mission="Research evidence",
            instructions="Collect and verify evidence.",
            required_capabilities=frozenset(),
            verification_requirements=("source",),
            quality_checks=("accuracy",),
        )
    })


def make_proposal(tmp_path: Path):
    store = SkillLearningStore(tmp_path / "learning.db")
    evaluation = SkillEvaluation(
        skill_id="research",
        skill_version="1.0.0",
        mission_id="mission-1",
        task_id="task-1",
        role="researcher",
        score=0.2,
        status="failed",
        model="model-a",
        verification_passed=(),
        verification_failed=("source",),
        verification_missing=(),
        quality_passed=(),
        quality_failed=("accuracy",),
        quality_missing=(),
        failure_signature="bad evidence",
        evidence_completeness=1.0,
        evidence_state="evidence_complete",
    )
    store.record(evaluation)
    store.record(evaluation)
    store.record(evaluation)
    proposal = SkillEvolutionEngine(store).observe(evaluation, base_version="1.0.0")
    assert proposal is not None
    return store, proposal


def test_skill_evolution_requires_explicit_approval_and_promotes_version(tmp_path: Path):
    store, proposal = make_proposal(tmp_path)
    runtime = SkillEvolutionRuntime(store, tmp_path / "evolution.db")
    registry = make_registry()

    prepared = runtime.prepare(
        proposal=proposal,
        registry=registry,
        improved_instructions="Use independent source cross-checking before completion.",
    )
    assert prepared.status == "validated"
    assert prepared.skill.version == "1.0.1"
    assert registry.resolve("research").version == "1.0.0"

    approved = runtime.approve(prepared)
    assert approved.status == "approved"

    promoted = runtime.promote(approved, registry)
    assert promoted.resolve("research").version == "1.0.1"
    assert "independent source cross-checking" in promoted.resolve("research").instructions


def test_skill_evolution_rejects_stale_base_version(tmp_path: Path):
    store, proposal = make_proposal(tmp_path)
    runtime = SkillEvolutionRuntime(store, tmp_path / "evolution.db")
    newer = SkillRegistry({
        "research": SkillSpec(
            skill_id="research",
            version="1.0.1",
            name="Research",
            mission="Research evidence",
            instructions="Newer procedure.",
            required_capabilities=frozenset(),
            verification_requirements=("source",),
            quality_checks=("accuracy",),
        )
    })

    try:
        runtime.prepare(proposal=proposal, registry=newer)
    except RuntimeError as exc:
        assert "version changed" in str(exc)
    else:
        raise AssertionError("stale evolution proposal was accepted")


class ValidationRouter:
    def generate(self, request, preferred_models=()):
        content = request.messages[-1]["content"]
        state = "pass" if "independent source cross-checking" in content else "fail"
        return InferenceResponse(
            model="validation-model",
            provider="test",
            content=f"VERIFY:source={state}\nQUALITY:accuracy={state}",
        )


def test_skill_evolution_rejects_candidate_without_measured_improvement(tmp_path: Path):
    store, proposal = make_proposal(tmp_path)
    runtime = SkillEvolutionRuntime(
        store, tmp_path / "evolution.db",
        validation_lab=SkillValidationLab(ValidationRouter()),
    )
    registry = make_registry()
    try:
        runtime.prepare(
            proposal=proposal,
            registry=registry,
            improved_instructions="Still collect evidence.",
        )
    except RuntimeError as exc:
        assert "validation lab rejected candidate" in str(exc)
    else:
        raise AssertionError("non-improving candidate reached approval")
