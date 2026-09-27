from pathlib import Path

from aurora.inference.model_performance import ModelPerformanceStore


def test_scoped_model_scores_ignore_other_task_types_and_incomplete_evidence(tmp_path: Path):
    store = ModelPerformanceStore(tmp_path / "model-performance.db")

    for model, score in (("model-a", 0.95), ("model-b", 0.70)):
        store.record(
            model=model,
            skill_id="research",
            skill_version="1.0.0",
            task_type="research",
            context_bucket="medium",
            score=score,
            evidence_completeness=1.0,
            evidence_state="evidence_complete",
            created_at="2026-09-27T00:00:00+00:00",
        )
        store.record(
            model=model,
            skill_id="research",
            skill_version="1.0.0",
            task_type="engineering",
            context_bucket="medium",
            score=0.20 if model == "model-a" else 0.90,
            evidence_completeness=1.0,
            evidence_state="evidence_complete",
            created_at="2026-09-27T00:01:00+00:00",
        )

    store.record(
        model="model-a",
        skill_id="research",
        skill_version="1.0.0",
        task_type="research",
        context_bucket="medium",
        score=0.0,
        evidence_completeness=0.2,
        evidence_state="insufficient_evidence",
        created_at="2026-09-27T00:02:00+00:00",
    )

    scores = store.trusted_scores_for(
        skill_ids=("research",),
        task_type="research",
        context_bucket="medium",
        minimum_evaluations=1,
    )

    assert scores == {"model-a": 0.95, "model-b": 0.70}


def test_context_bucket_is_part_of_model_learning_scope(tmp_path: Path):
    store = ModelPerformanceStore(tmp_path / "model-performance.db")
    for model, score in (("model-a", 0.9), ("model-b", 0.6)):
        store.record(
            model=model,
            skill_id="research",
            skill_version="1.0.0",
            task_type="research",
            context_bucket="small",
            score=score,
            evidence_completeness=1.0,
            evidence_state="evidence_complete",
            created_at="2026-09-27T00:00:00+00:00",
        )
        store.record(
            model=model,
            skill_id="research",
            skill_version="1.0.0",
            task_type="research",
            context_bucket="large",
            score=0.6 if model == "model-a" else 0.9,
            evidence_completeness=1.0,
            evidence_state="evidence_complete",
            created_at="2026-09-27T00:01:00+00:00",
        )

    assert store.trusted_scores_for(
        skill_ids=("research",), task_type="research", context_bucket="large",
        minimum_evaluations=1,
    ) == {"model-a": 0.6, "model-b": 0.9}


def test_skill_version_is_part_of_model_learning_scope(tmp_path: Path):
    store = ModelPerformanceStore(tmp_path / "model-performance.db")
    store.record(
        model="model-a",
        skill_id="research",
        skill_version="1.0.0",
        task_type="research",
        context_bucket="medium",
        score=0.95,
        evidence_completeness=1.0,
        evidence_state="evidence_complete",
        created_at="2026-09-27T00:00:00+00:00",
    )
    store.record(
        model="model-a",
        skill_id="research",
        skill_version="2.0.0",
        task_type="research",
        context_bucket="medium",
        score=0.20,
        evidence_completeness=1.0,
        evidence_state="evidence_complete",
        created_at="2026-09-27T00:01:00+00:00",
    )

    assert store.trusted_scores_for(
        skill_ids=("research",),
        skill_versions={"research": "1.0.0"},
        task_type="research",
        context_bucket="medium",
        minimum_evaluations=1,
    ) == {"model-a": 0.95}

    assert store.trusted_scores_for(
        skill_ids=("research",),
        skill_versions={"research": "2.0.0"},
        task_type="research",
        context_bucket="medium",
        minimum_evaluations=1,
    ) == {"model-a": 0.20}
