from pathlib import Path

from aurora.inference.models import InferenceResponse
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.task_engine import DelegatedTask, DelegatedTaskResult, TaskStatus
from aurora.skills import SkillRegistry
from aurora.skills.evaluation import SkillEvaluator
from aurora.skills.learning import SkillEvolutionEngine, SkillLearningStore
from aurora.skills.models import SkillSpec


def _registry() -> tuple[SkillRegistry, OrganizationRegistry]:
    skills = SkillRegistry({
        "research": SkillSpec(
            "research", "1.0.0", "Research", "m", "Use evidence.\nVERIFY:source_check=pass",
            frozenset(), ("source_check",), ("citation_check",), ("research",),
        )
    })
    organization = OrganizationRegistry(
        agents={"researcher": AgentSpec("researcher","researcher","research","m",frozenset(),(),("research",))},
        departments={"research": DepartmentSpec("research","m",("researcher",),(),frozenset())},
    )
    return skills, organization


def test_skill_evaluator_scores_observable_evidence():
    skills, org = _registry()
    evaluator = SkillEvaluator(skills)
    from aurora.orchestration.engine import OrchestrationEngine, TaskResult
    from aurora.inference.models import InferenceRequest

    response = InferenceResponse(
        model="model/a",
        provider="fake",
        content="result\nVERIFY:source_check=pass\nQUALITY:citation_check=pass",
    )
    result = DelegatedTaskResult(
        task=DelegatedTask("t","researcher","research"),
        status=TaskStatus.SUCCEEDED,
        model_result=TaskResult(
            task_id="t",
            role="researcher",
            response=response,
        ),
    )
    evaluations = evaluator.evaluate(
        task=result.task,
        result=result,
        default_skill_ids=("research",),
        agent_capabilities=frozenset(),
    )
    assert evaluations[0].score > 0
    assert evaluations[0].verification_passed == ("source_check",)
    assert evaluations[0].verification_missing == ()
    assert evaluations[0].quality_missing == ("citation_check",)


def test_learning_store_builds_model_scores_and_proposal(tmp_path: Path):
    store = SkillLearningStore(tmp_path/"skills.db")
    from aurora.skills.evaluation import SkillEvaluation
    for score in (0.4, 0.5, 0.6):
        store.record(SkillEvaluation(
            "research","1.0.0",None,f"t-{score}","researcher",score,"succeeded","model/a",
            ("source_check",),(),("citation_check",),("citation_check",),(),(),None
        ))
    perf = store.performance("research")
    assert perf.evaluations == 3
    assert perf.average_score == 0.5
    assert store.model_scores_for(("research",)) == {"model/a": 0.5}
    proposal = SkillEvolutionEngine(store).observe(
        SkillEvaluation("research","1.0.0",None,"t-4","researcher",0.4,"failed","model/a",
                        (),(),("source_check",),(),(),("citation_check",),"boom"),
        base_version="1.0.0",
    )
    assert proposal is not None
    assert proposal.candidate_version == "1.0.1"
