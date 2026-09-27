import pytest

from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.router import ModelRouter, ModelRoutingError


def _model(
    model_id: str,
    *,
    specialties=(),
    quality=0.5,
    latency=0.5,
    cost=0.5,
    context=8192,
    healthy=True,
    available=True,
):
    return ModelSpec(
        id=model_id,
        provider="nvidia_api",
        endpoint="https://example.invalid",
        capabilities=("reasoning",),
        context_window=context,
        specialties=tuple(specialties),
        quality_score=quality,
        latency_score=latency,
        cost_score=cost,
        healthy=healthy,
        available=available,
    )


def test_skill_and_work_context_drive_selection():
    router = ModelRouter([
        _model("general", quality=0.8, specialties=("general",)),
        _model("research", specialties=("research", "evidence")),
    ])
    request = InferenceRequest(
        messages=[{"role": "user", "content": "research"}],
        metadata={
            "skill_tags": ["research", "evidence"],
            "task_type": "research",
        },
    )

    assert router.select(request, capability="reasoning").id == "research"


def test_trusted_learned_history_can_change_model_selection():
    router = ModelRouter([
        _model("baseline", quality=0.9),
        _model("learner", quality=0.5),
    ])
    request = InferenceRequest(
        messages=[{"role": "user", "content": "work"}],
        metadata={"learned_model_scores": {"baseline": 0.2, "learner": 1.0}},
    )

    assert router.select(request, capability="reasoning").id == "learner"


def test_unhealthy_or_unavailable_models_are_excluded():
    router = ModelRouter([
        _model("unhealthy", quality=1.0, healthy=False),
        _model("offline", quality=1.0, available=False),
        _model("ready", quality=0.2),
    ])
    request = InferenceRequest(messages=[{"role": "user", "content": "work"}])

    assert [model.id for model in router.available()] == ["ready"]
    assert router.select(request, capability="reasoning").id == "ready"


def test_request_constraints_filter_models_before_scoring():
    router = ModelRouter([
        _model("cheap", quality=0.95, cost=0.4),
        _model("qualified", quality=0.7, cost=0.9),
    ])
    request = InferenceRequest(
        messages=[{"role": "user", "content": "work"}],
        metadata={"min_cost_score": 0.8},
    )

    assert router.select(request, capability="reasoning").id == "qualified"


def test_context_window_is_a_hard_routing_boundary():
    router = ModelRouter([
        _model("small", context=1024, quality=1.0),
        _model("large", context=16384, quality=0.5),
    ])
    request = InferenceRequest(
        messages=[{"role": "user", "content": "work"}],
        metadata={"context_tokens": 4096},
    )

    assert router.select(request, capability="reasoning").id == "large"


def test_ties_are_broken_deterministically_by_model_id():
    router = ModelRouter([
        _model("model-b"),
        _model("model-a"),
    ])
    request = InferenceRequest(messages=[{"role": "user", "content": "work"}])

    assert router.select(request, capability="reasoning").id == "model-b"


def test_routing_decision_exposes_auditable_factors_and_ranked_candidates():
    router = ModelRouter([
        _model("general", quality=0.8, specialties=("general",)),
        _model("research", specialties=("research", "evidence")),
    ])
    request = InferenceRequest(
        messages=[{"role": "user", "content": "research"}],
        metadata={
            "skill_tags": ["research", "evidence"],
            "task_type": "research",
            "learned_model_scores": {"research": 0.9},
        },
    )

    decision = router.route(request, capability="reasoning")

    assert decision.selected_model == "research"
    assert decision.selected_score > 0.0
    assert decision.candidates[0][0] == "research"
    assert decision.factors["skill_tag_overlap"] == 1.0
    assert decision.factors["work_type_match"] == 1.0
    assert decision.factors["learned_score"] == 0.9


def test_requested_model_must_be_healthy_available_and_within_constraints():
    router = ModelRouter([_model("target", healthy=False)])

    with pytest.raises(ModelRoutingError, match="unavailable"):
        router.select(
            InferenceRequest(messages=[{"role": "user", "content": "work"}], model="target"),
            capability="reasoning",
        )


def test_no_candidate_after_constraints_fails_closed():
    router = ModelRouter([_model("only", cost=0.2)])

    with pytest.raises(ModelRoutingError, match="No enabled"):
        router.select(
            InferenceRequest(
                messages=[{"role": "user", "content": "work"}],
                metadata={"min_cost_score": 0.8},
            ),
            capability="reasoning",
        )
