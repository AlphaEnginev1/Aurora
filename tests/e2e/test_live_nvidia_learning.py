"""Live NVIDIA adaptive-learning gate.

This test is intentionally opt-in. It runs only when a real NVIDIA API key,
endpoint, and served model are supplied by the dedicated workflow.
"""

import os
from pathlib import Path

import pytest

from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import DelegatedTask, DelegatedTaskResult, TaskStatus
from aurora.skills import SkillRegistry
from aurora.skills.evaluation import SkillEvaluator
from aurora.skills.models import SkillSpec


def test_live_nvidia_execution_evaluates_and_persists_learning(tmp_path: Path):
    api_key = os.environ.get("NVIDIA_API_KEY")
    endpoint = os.environ.get("NVIDIA_API_ENDPOINT", "https://integrate.api.nvidia.com/v1")
    model_id = os.environ.get("NVIDIA_MODEL_ID")
    if not api_key or not model_id:
        pytest.skip("live NVIDIA gate requires NVIDIA_API_KEY and NVIDIA_MODEL_ID")

    spec = ModelSpec(
        model_id,
        "nvidia_api",
        endpoint,
        quality_score=0.60,
        latency_score=0.60,
        cost_score=0.60,
        specialties=("research",),
    )
    router = ModelRouter([spec], nvidia_api_key=api_key)
    request = InferenceRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a deterministic Aurora integration-test worker. "
                    "Answer with one short sentence, then on separate lines output exactly "
                    "VERIFY:source_check=pass and QUALITY:citation_check=pass."
                ),
            },
            {"role": "user", "content": "Confirm the Aurora provider-learning test is running."},
        ],
        temperature=0,
        max_tokens=80,
        metadata={"skill_tags": ("research",), "task_type": "research"},
    )

    evaluator = SkillEvaluator(
        SkillRegistry(
            {
                "research": SkillSpec(
                    "research",
                    "1.0.0",
                    "Research specialist",
                    "Produce verified evidence.",
                    "Provide citation-quality evidence.",
                    frozenset(),
                    ("source_check",),
                    ("citation_check",),
                    ("research",),
                )
            }
        )
    )
    task = DelegatedTask(
        "live-nvidia-learning",
        "researcher",
        "research task",
        skills=("research",),
    )
    performance = ModelPerformanceStore(tmp_path / "model-performance.db")

    for attempt in range(2):
        response = router.generate(request)
        assert response.provider == "nvidia_api"
        assert response.model == model_id
        assert response.provider_model == model_id
        assert response.reported_model
        assert response.telemetry is not None
        assert response.telemetry.success is True
        assert response.telemetry.request_id == request.request_id
        assert response.telemetry.execution_id == response.execution_id
        assert response.usage.total_tokens is not None
        assert "VERIFY:source_check=pass" in response.content
        assert "QUALITY:citation_check=pass" in response.content

        evaluated = evaluator.evaluate(
            task=task,
            result=DelegatedTaskResult(
                task=task,
                status=TaskStatus.SUCCEEDED,
                model_result=TaskResult(
                    task_id=f"live-nvidia-{attempt}",
                    role="researcher",
                    response=response,
                ),
            ),
            task_type="research",
            context_bucket="small",
        )
        evaluation = evaluated[0]
        assert evaluation.model == model_id
        assert evaluation.evidence_state == "evidence_complete"
        assert evaluation.evidence_completeness == 1.0
        assert evaluation.score == 1.0
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
    assert learned == {model_id: 1.0}

    adapted = router.route(
        InferenceRequest(
            messages=[{"role": "user", "content": "research task"}],
            metadata={
                "skill_tags": ("research",),
                "task_type": "research",
                "learned_model_scores": learned,
            },
        )
    )
    assert adapted.selected_model == model_id
    assert adapted.factors["learned_score"] == 1.0
