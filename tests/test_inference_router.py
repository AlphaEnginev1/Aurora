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
