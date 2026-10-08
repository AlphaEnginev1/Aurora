from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.router import ModelRouter


def test_router_honors_department_model_preferences():
    models = [
        ModelSpec(
            "model/slow",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            enabled=True,
        ),
        ModelSpec(
            "model/preferred",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            enabled=True,
        ),
    ]
    router = ModelRouter(models)
    selected = router.select(
        InferenceRequest(messages=[{"role": "user", "content": "x"}]),
        capability="reasoning",
        preferred_models=("model/preferred", "model/slow"),
    )
    assert selected.id == "model/preferred"


def test_router_falls_back_when_preferred_model_is_disabled():
    models = [
        ModelSpec(
            "model/preferred",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            enabled=False,
        ),
        ModelSpec(
            "model/fallback",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            enabled=True,
        ),
    ]
    router = ModelRouter(models)
    selected = router.select(
        InferenceRequest(messages=[{"role": "user", "content": "x"}]),
        capability="reasoning",
        preferred_models=("model/preferred",),
    )
    assert selected.id == "model/fallback"


def test_router_rejects_explicit_model_that_lacks_requested_capability():
    models = [
        ModelSpec(
            "model/no-reasoning",
            "fake",
            "https://example.invalid",
            capabilities=("coding",),
            enabled=True,
        )
    ]
    router = ModelRouter(models)
    try:
        router.select(
            InferenceRequest(messages=[{"role": "user", "content": "x"}], model="model/no-reasoning"),
            capability="reasoning",
        )
    except Exception as exc:
        assert "does not satisfy capability" in str(exc)
    else:
        raise AssertionError("incompatible explicit model unexpectedly accepted")
 
 
def test_router_selects_model_from_skill_and_work_affinity():
    models = [
        ModelSpec(
            "model/general",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            specialties=("engineering",),
            quality_score=0.95,
            latency_score=0.8,
            cost_score=0.8,
        ),
        ModelSpec(
            "model/research",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            specialties=("research", "science"),
            quality_score=0.6,
            latency_score=0.5,
            cost_score=0.5,
        ),
    ]
    router = ModelRouter(models)
    selected = router.select(
        InferenceRequest(
            messages=[{"role": "user", "content": "analyze evidence"}],
            metadata={"skill_tags": ["research", "science"], "task_type": "research"},
        ),
        capability="reasoning",
    )
    assert selected.id == "model/research"


def test_router_uses_learned_skill_performance():
    models = [
        ModelSpec(
            "model/a",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            quality_score=0.8,
            specialties=("research",),
        ),
        ModelSpec(
            "model/b",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            quality_score=0.8,
            specialties=("research",),
        ),
    ]
    router = ModelRouter(models)
    selected = router.select(
        InferenceRequest(
            messages=[{"role": "user", "content": "research"}],
            metadata={
                "skill_tags": ["research"],
                "task_type": "research",
                "learned_model_scores": {"model/a": 0.2, "model/b": 0.95},
            },
        ),
        capability="reasoning",
    )
    assert selected.id == "model/b"


def test_free_only_router_rejects_paid_and_unknown_models():
    models = [
        ModelSpec("model/free", "fake", "https://example.invalid", capabilities=("reasoning",), pricing_tier="free"),
        ModelSpec("model/unknown", "fake", "https://example.invalid", capabilities=("reasoning",), pricing_tier="unknown"),
        ModelSpec("model/paid", "fake", "https://example.invalid", capabilities=("reasoning",), pricing_tier="paid"),
    ]
    router = ModelRouter(models, cost_policy="free_only")
    assert [item.id for item in router.available()] == ["model/free"]
    assert router.select(
        InferenceRequest(messages=[{"role": "user", "content": "x"}]),
        capability="reasoning",
    ).id == "model/free"
    try:
        router.select(
            InferenceRequest(messages=[{"role": "user", "content": "x"}], model="model/paid"),
            capability="reasoning",
        )
    except Exception as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("paid model unexpectedly routed under free_only policy")


def test_router_retries_next_ranked_model_after_provider_failure():
    from aurora.inference.models import InferenceResponse
    from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
    from aurora.inference.errors import ProviderHTTPError, ProviderErrorContext

    attempts = []

    class FakeProvider:
        provider_id = "fake"

        def __init__(self, spec):
            self.spec = spec

        def generate(self, request, *, selected_model=None, execution_id=""):
            attempts.append(selected_model)
            if selected_model == "model/a":
                raise ProviderHTTPError(
                    "forced provider failure",
                    context=ProviderErrorContext(
                        provider="fake",
                        request_id=request.request_id,
                        execution_id=execution_id,
                        requested_model=request.model,
                        selected_model=selected_model,
                        provider_model=selected_model,
                    ),
                    status_code=403,
                )
            return InferenceResponse(
                model=selected_model,
                provider="fake",
                content="ok",
                raw={},
                request_id=request.request_id,
                execution_id=execution_id,
                requested_model=request.model,
                selected_model=selected_model,
                provider_model=selected_model,
            )

    registry = ProviderRegistry((
        ProviderSpec("fake", "Fake", ("chat", "reasoning"), True, FakeProvider),
    ))
    models = [
        ModelSpec(
            "model/a",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            enabled=True,
        ),
        ModelSpec(
            "model/b",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
            enabled=True,
        ),
    ]
    router = ModelRouter(models, provider_registry=registry)
    response = router.generate(
        InferenceRequest(messages=[{"role": "user", "content": "x"}]),
        capability="reasoning",
        preferred_models=("model/a", "model/b"),
    )

    assert response.content == "ok"
    assert attempts == ["model/a", "model/b"]
