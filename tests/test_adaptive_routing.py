from pathlib import Path

from aurora.inference.models import InferenceRequest, InferenceResponse, ModelSpec
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import DelegatedTask, DelegatedTaskResult, TaskStatus
from aurora.skills import SkillRegistry
from aurora.skills.evaluation import SkillEvaluator
from aurora.skills.models import SkillSpec


class ControlledProviderRouter(ModelRouter):
    """Real routing logic with a deterministic provider for the proof."""

    def generate(self, request, capability=None, preferred_models=None):
        decision = self.route(
            request,
            capability=capability,
            preferred_models=preferred_models,
        )
        return InferenceResponse(
            model=decision.selected_model,
            provider="controlled-test-provider",
            content="result\nVERIFY:source_check=pass\nQUALITY:citation_check=pass",
        )


def test_trusted_execution_changes_subsequent_model_selection(tmp_path: Path):
    """A real evaluated execution must be able to change the next routing decision."""
    models = [
        ModelSpec(
            "model/a",
            "controlled",
            "https://example.invalid/a",
            quality_score=0.80,
            latency_score=0.80,
            cost_score=0.80,
            specialties=("research",),
        ),
        ModelSpec(
            "model/b",
            "controlled",
            "https://example.invalid/b",
            quality_score=0.60,
            latency_score=0.60,
            cost_score=0.60,
            specialties=("research",),
        ),
    ]
    router = ControlledProviderRouter(models)
    baseline_request = InferenceRequest(
        messages=[{"role": "user", "content": "research task"}],
        metadata={
            "skill_tags": ("research",),
            "task_type": "research",
        },
    )

    baseline = router.route(baseline_request, preferred_models=())
    assert baseline.selected_model == "model/a"

    registry = SkillRegistry(
        {
            "research": SkillSpec(
                "research",
                "1.0.0",
                "Research specialist",
                "Research using verified evidence.",
                "Use verified evidence.",
                frozenset(),
                ("source_check",),
                ("citation_check",),
                ("research",),
            )
        }
    )
    evaluator = SkillEvaluator(registry)
    performance = ModelPerformanceStore(tmp_path / "model-performance.db")
    task = DelegatedTask(
        "adaptive-learning",
        "researcher",
        "research task",
        skills=("research",),
    )

    for attempt in range(2):
        forced = router.generate(
            InferenceRequest(
                model="model/b",
                messages=[{"role": "user", "content": "research task"}],
                metadata={
                    "skill_tags": ("research",),
                    "task_type": "research",
                },
            )
        )
        assert forced.model == "model/b"
        evaluated = evaluator.evaluate(
            task=task,
            result=DelegatedTaskResult(
                task=task,
                status=TaskStatus.SUCCEEDED,
                model_result=TaskResult(
                    task_id=f"adaptive-learning-{attempt}",
                    role="researcher",
                    response=forced,
                ),
            ),
            task_type="research",
            context_bucket="small",
        )
        assert len(evaluated) == 1
        evaluation = evaluated[0]
        assert evaluation.model == "model/b"
        assert evaluation.score == 1.0
        assert evaluation.evidence_completeness == 1.0
        assert evaluation.evidence_state == "evidence_complete"
        performance.record(
            model=evaluation.model,
            skill_id=evaluation.skill_id,
            skill_version=evaluation.skill_version,
            task_type=evaluation.task_type,
            context_bucket=evaluation.context_bucket,
            score=evaluation.score,
            evidence_completeness=evaluation.evidence_completeness,
            evidence_state=evaluation.evidence_state,
            created_at=f"2026-09-28T00:00:0{attempt}+00:00",
        )

    learned = performance.trusted_scores_for(
        skill_ids=("research",),
        skill_versions={"research": "1.0.0"},
        task_type="research",
        context_bucket="small",
    )
    assert learned == {"model/b": 1.0}

    adapted_request = InferenceRequest(
        messages=[{"role": "user", "content": "research task"}],
        metadata={
            "skill_tags": ("research",),
            "task_type": "research",
            "learned_model_scores": learned,
        },
    )
    adapted = router.route(adapted_request, preferred_models=())

    assert adapted.selected_model == "model/b"
    assert adapted.selected_model != baseline.selected_model
    assert adapted.factors["learned_score"] == 1.0
    assert adapted.candidates[0][0] == "model/b"
