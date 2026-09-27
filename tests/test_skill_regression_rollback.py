from pathlib import Path

from aurora.skills.evolution_runtime import SkillEvolutionRuntime
from aurora.skills.learning import SkillEvolutionEngine, SkillLearningStore
from aurora.skills.models import SkillSpec
from aurora.skills.registry import SkillRegistry
from aurora.skills.evaluation import SkillEvaluation


def registry(version: str, instructions: str = "Collect and verify evidence.") -> SkillRegistry:
    return SkillRegistry({"research": SkillSpec(
        skill_id="research", version=version, name="Research",
        mission="Research evidence", instructions=instructions,
        required_capabilities=frozenset(), verification_requirements=("source",),
        quality_checks=("accuracy",),
    )})


def evaluation(version: str, score: float, task: str) -> SkillEvaluation:
    return SkillEvaluation(
        skill_id="research", skill_version=version, mission_id="m", task_id=task,
        role="researcher", score=score, status="succeeded",
        model="model-a", verification_passed=("source",), verification_failed=(),
        verification_missing=(), quality_passed=("accuracy",), quality_failed=(),
        quality_missing=(), failure_signature=None, evidence_completeness=1.0,
        evidence_state="evidence_complete",
    )


def test_regression_gate_and_exact_rollback(tmp_path: Path):
    learning = SkillLearningStore(tmp_path / "learning.db")
    for i in range(3):
        learning.record(evaluation("1.0.0", 0.9, f"base-{i}"))
        learning.record(evaluation("1.0.1", 0.5, f"candidate-{i}"))

    engine = SkillEvolutionEngine(learning)
    proposal = engine.observe(evaluation("1.0.0", 0.2, "failure"), base_version="1.0.0")
    assert proposal is not None

    runtime = SkillEvolutionRuntime(learning, tmp_path / "evolution.db")
    prepared = runtime.prepare(proposal=proposal, registry=registry("1.0.0"))
    approved = runtime.approve(prepared)
    promoted = runtime.promote(approved, registry("1.0.0"))

    assert runtime.regression_status(
        skill_id="research",
        candidate_version="1.0.1",
        baseline_version="1.0.0",
    ) == "regressed"

    rolled = runtime.rollback(prepared.candidate_id, promoted)
    assert rolled.resolve("research").version == "1.0.0"
    assert rolled.resolve("research").instructions == "Collect and verify evidence."


def test_regression_gate_fails_closed_without_enough_evidence(tmp_path: Path):
    learning = SkillLearningStore(tmp_path / "learning.db")
    for i in range(3):
        learning.record(evaluation("1.0.0", 0.9, f"base-{i}"))
    runtime = SkillEvolutionRuntime(learning, tmp_path / "evolution.db")
    assert runtime.regression_status(
        skill_id="research", candidate_version="1.0.1",
        baseline_version="1.0.0",
    ) == "insufficient_evidence"
