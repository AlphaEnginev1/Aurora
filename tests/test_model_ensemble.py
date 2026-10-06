from pathlib import Path

from aurora.inference import InferenceRequest, InferenceResponse, ModelEnsemble, ModelEnsembleStore, ModelRouter, ModelSpec


def _router():
    models = [
        ModelSpec(f"model/{name}", "fake", "https://example.invalid", capabilities=("reasoning",), enabled=True, healthy=True, available=True)
        for name in ("a", "b", "c", "d")
    ]
    router = ModelRouter(models)
    outputs = {
        "model/a": "same decision",
        "model/b": "same decision",
        "model/c": "different decision",
        "model/d": "resolved decision",
    }
    captured = []

    def generate(request, *, capability=None, preferred_models=None):
        decision = router.route(request, capability=capability, preferred_models=preferred_models)
        model = decision.selected_model
        captured.append(request)
        content = outputs[model]
        return InferenceResponse(
            model=model,
            provider="fake",
            content=content,
            request_id=request.request_id,
            execution_id=f"exec-{model}",
            selected_model=model,
            routing_score=decision.selected_score,
        )

    router.generate = generate
    router.captured_requests = captured
    return router


def test_critical_ensemble_reaches_consensus_and_persists(tmp_path: Path):
    store = ModelEnsembleStore(tmp_path / "tasks.db")
    ensemble = ModelEnsemble(_router(), store=store)

    result = ensemble.run_critical_decision(
        InferenceRequest(messages=[{"role": "user", "content": "choose"}]),
        ensemble_size=2,
    )

    assert result.status == "consensus"
    assert result.decision == "same decision"
    assert result.resolver_model is None
    assert result.agreement_ratio == 1.0

    persisted = store.get(result.ensemble_id)
    assert persisted is not None
    assert persisted.status == "consensus"
    assert tuple(item.model for item in persisted.members) == ("model/a", "model/b")


def test_critical_ensemble_resolves_disagreement_with_unused_dynamic_model():
    router = _router()
    ensemble = ModelEnsemble(router)

    result = ensemble.run_critical_decision(
        InferenceRequest(messages=[{"role": "user", "content": "choose"}]),
        ensemble_size=3,
    )

    assert result.status == "resolved"
    assert result.resolver_model == "model/d"
    assert result.decision == "resolved decision"
    assert tuple(item.model for item in result.members) == (
        "model/a",
        "model/b",
        "model/c",
    )
    resolver_request = router.captured_requests[-1]
    assert resolver_request.model is None
    assert resolver_request.metadata["ensemble_mode"] == "critical_resolver"
    assert "MODEL 1: model/a" in resolver_request.messages[-1]["content"]


def test_critical_ensemble_fails_closed_when_disagreement_has_no_unused_resolver():
    ensemble = ModelEnsemble(_router())

    result = ensemble.run_critical_decision(
        InferenceRequest(
            messages=[{"role": "user", "content": "choose"}],
            metadata={"allowed_models": ("model/a", "model/c")},
        ),
        ensemble_size=2,
    )

    assert result.status == "disagreement"
    assert result.decision is None
    assert result.resolver_model is None
    assert result.agreement_ratio == 0.5


def test_critical_ensemble_rejects_manual_model_selection():
    ensemble = ModelEnsemble(_router())

    try:
        ensemble.run_critical_decision(
            InferenceRequest(
                messages=[{"role": "user", "content": "choose"}],
                model="model/a",
            )
        )
    except Exception as exc:
        assert "dynamic model selection" in str(exc)
    else:
        raise AssertionError("manual model selection must be rejected")
