from pathlib import Path

import pytest

from aurora.inference.errors import ProviderTimeoutError
from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.provider_health import ProviderHealthStore
from aurora.inference.reserve_pool import DynamicReservePool, DynamicReservePoolError
from aurora.inference.router import ModelRouter


def _model(model_id, *, provider="nvidia_api", quality=0.5, latency=0.5, specialties=()):
    return ModelSpec(
        id=model_id,
        provider=provider,
        endpoint="https://example.test/v1",
        capabilities=("chat", "reasoning"),
        specialties=tuple(specialties),
        quality_score=quality,
        latency_score=latency,
        cost_score=1.0,
        pricing_tier="free",
    )


def _router(tmp_path: Path):
    health = ProviderHealthStore(tmp_path / "health.db", failure_threshold=1)
    models = [
        _model("m1", quality=0.9, specialties=("research",)),
        _model("m2", quality=0.8, specialties=("research",)),
        _model("m3", quality=0.7, specialties=("research",)),
    ]
    return ModelRouter(models=models, health_store=health, cost_policy="free_only"), health


def test_dynamic_pool_ranks_by_existing_router_score_and_deduplicates(tmp_path):
    router, _health = _router(tmp_path)
    pool = DynamicReservePool(router, max_candidates=3)
    request = InferenceRequest(
        messages=[{"role": "user", "content": "research"}],
        metadata={"task_type": "research"},
    )

    candidates = pool.build(
        request,
        models=[router.models[1], router.models[0], router.models[0], router.models[2]],
        capability="reasoning",
    )

    assert [item.model.id for item in candidates] == ["m1", "m2", "m3"]
    assert len({item.key for item in candidates}) == 3


def test_dynamic_pool_excludes_cooldown_models_and_fails_closed_when_empty(tmp_path):
    router, health = _router(tmp_path)
    health.record_failure("nvidia_api", "m1", ProviderTimeoutError("timeout"))

    pool = DynamicReservePool(router)
    request = InferenceRequest(messages=[{"role": "user", "content": "hello"}])

    candidates = pool.build(request, models=router.models, capability="reasoning")
    assert [item.model.id for item in candidates] == ["m2", "m3"]

    empty = pool.build(request, models=router.models, capability="tool_use")
    assert empty == ()


def test_dynamic_pool_honors_provider_diversity_and_learned_scores(tmp_path):
    health = ProviderHealthStore(tmp_path / "health.db")
    models = [
        _model("nvidia-a", provider="nvidia_api", quality=0.6),
        _model("nvidia-b", provider="nvidia_api", quality=0.95),
        _model("openai-a", provider="openai", quality=0.7),
    ]
    router = ModelRouter(models=models, health_store=health, cost_policy="free_only")
    pool = DynamicReservePool(router, max_candidates=3, max_per_provider=1)
    request = InferenceRequest(messages=[{"role": "user", "content": "hello"}])

    # free_only remains a policy guard, so the external provider is intentionally
    # excluded until its pricing tier is governed as free.
    candidates = pool.build(
        request,
        models=models,
        capability="reasoning",
        learned_scores={"nvidia-a": 1.0, "nvidia-b": 0.0},
    )
    assert [item.model.id for item in candidates] == ["nvidia-a"]


def test_dynamic_pool_applies_allowlist_and_exclusions(tmp_path):
    router, _health = _router(tmp_path)
    pool = DynamicReservePool(router)
    request = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        metadata={"allowed_models": ["m1", "m2"]},
    )

    candidates = pool.build(
        request,
        models=router.models,
        exclude=[("nvidia_api", "m1")],
    )
    assert [item.model.id for item in candidates] == ["m2"]


def test_dynamic_pool_rejects_invalid_multimodal_request(tmp_path):
    router, _health = _router(tmp_path)
    pool = DynamicReservePool(router)
    request = InferenceRequest(messages=[{"role": "user", "content": object()}])

    with pytest.raises(DynamicReservePoolError):
        pool.build(request)
