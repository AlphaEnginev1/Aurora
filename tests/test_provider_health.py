from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aurora.inference.errors import ProviderErrorContext, ProviderHTTPError, ProviderTimeoutError
from aurora.inference.models import InferenceRequest, InferenceResponse, ModelSpec
from aurora.inference.provider_health import ProviderHealthStore, classify_failure
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
from aurora.inference.router import ModelRouter


def _ctx(provider="test", model="m1"):
    return ProviderErrorContext(
        provider=provider,
        request_id="req",
        execution_id="exec",
        selected_model=model,
        provider_model=model,
    )


def test_health_state_machine_persists_and_recovers(tmp_path: Path):
    store = ProviderHealthStore(tmp_path / "runtime.db", base_cooldown_seconds=1, max_cooldown_seconds=10)
    snap = store.snapshot("test", "m1")
    assert snap.state == "healthy"

    store.record_failure("test", "m1", ProviderTimeoutError("timeout", context=_ctx()))
    assert store.snapshot("test", "m1").state == "degraded"

    snap = store.record_failure("test", "m1", ProviderHTTPError("rate limited", context=_ctx(), status_code=429))
    assert snap.state == "cooldown"
    assert not snap.routable

    with store._connect() as db:
        db.execute(
            "UPDATE provider_health SET cooldown_until=? WHERE provider_id=? AND model_id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), "test", "m1"),
        )
    assert store.snapshot("test", "m1").state == "probing"
    assert store.snapshot("test", "m1").routable

    recovered = store.record_success("test", "m1", latency_ms=12)
    assert recovered.state == "healthy"
    assert recovered.consecutive_failures == 0
    assert recovered.success_count == 1


def test_health_classification_separates_transient_from_configuration():
    timeout = ProviderTimeoutError("timeout", context=_ctx())
    assert classify_failure(timeout)[0] == "transient"
    rate_limit = ProviderHTTPError("429", context=_ctx(), status_code=429)
    assert classify_failure(rate_limit)[0] == "transient"
    auth = ProviderHTTPError("401", context=_ctx(), status_code=401)
    assert classify_failure(auth)[0] == "configuration"


class FakeProvider:
    provider_id = "test"

    def __init__(self, model: ModelSpec, failures: int = 0):
        self.spec = model
        self.failures = failures
        self.calls = 0

    def generate(self, request, *, selected_model, execution_id):
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderTimeoutError("timeout", context=_ctx(model=self.spec.id))
        return InferenceResponse(
            model=self.spec.id,
            provider=self.provider_id,
            content="ok",
            request_id=request.request_id,
            execution_id=execution_id,
            selected_model=selected_model,
            provider_model=self.spec.id,
        )


def test_router_uses_health_to_remove_cooled_model(tmp_path: Path):
    health = ProviderHealthStore(tmp_path / "health.db", failure_threshold=1)
    specs = [
        ModelSpec("m1", "test", "https://example.invalid", ("reasoning",), quality_score=1.0),
        ModelSpec("m2", "test", "https://example.invalid", ("reasoning",), quality_score=0.5),
    ]
    def factory(spec):
        return FakeProvider(spec, failures=1 if spec.id == "m1" else 0)
    registry = ProviderRegistry((
        ProviderSpec("test", "Test", ("chat", "reasoning"), True, factory),
    ))
    router = ModelRouter(specs, provider_registry=registry, health_store=health)
    request = InferenceRequest(messages=[{"role":"user","content":"x"}])

    assert router.route(request, capability="reasoning").selected_model == "m1"
    response = router.generate(request, capability="reasoning")
    assert response.content == "ok"
    assert response.selected_model == "m2"
    assert health.snapshot("test", "m1").state == "cooldown"
    assert router.route(request, capability="reasoning").selected_model == "m2"


def test_configuration_failure_reenters_probing_after_revalidation_window(tmp_path: Path):
    store = ProviderHealthStore(
        tmp_path / "runtime.db",
        failure_threshold=1,
        base_cooldown_seconds=1,
        max_cooldown_seconds=10,
    )
    failed = store.record_failure(
        "nvidia_api",
        "model/recovered",
        ProviderHTTPError(
            "unauthorized",
            context=_ctx(provider="nvidia_api", model="model/recovered"),
            status_code=401,
        ),
    )
    assert failed.state == "unavailable"
    assert not failed.routable
    assert not store.is_eligible("nvidia_api", "model/recovered")

    future = datetime.now(timezone.utc) + timedelta(seconds=11)
    probing = store.snapshot("nvidia_api", "model/recovered", now=future)
    assert probing.state == "probing"
    assert probing.routable

    recovered = store.record_success(
        "nvidia_api",
        "model/recovered",
        latency_ms=25,
    )
    assert recovered.state == "healthy"
    assert recovered.consecutive_failures == 0
    assert recovered.last_error_code is None


def test_provider_availability_aggregates_model_health(tmp_path: Path):
    store = ProviderHealthStore(tmp_path / "health.db", failure_threshold=1)
    store.record_success("nvidia_api", "m1", latency_ms=20)
    store.record_failure(
        "nvidia_api",
        "m2",
        ProviderTimeoutError("timeout", context=_ctx(provider="nvidia_api", model="m2")),
    )

    snapshot = store.provider_snapshot("nvidia_api")
    assert snapshot.state == "healthy"
    assert snapshot.model_count == 2
    assert snapshot.routable_model_count == 1
    assert snapshot.healthy_model_count == 1
    assert snapshot.cooldown_model_count == 1
    assert snapshot.routable
    assert snapshot.availability_score == 1.0

    available = store.available_providers(("nvidia_api", "missing"))
    assert [item.provider_id for item in available] == ["nvidia_api"]


def test_provider_availability_fails_closed_when_all_models_are_unavailable(tmp_path: Path):
    store = ProviderHealthStore(
        tmp_path / "health.db",
        failure_threshold=1,
        base_cooldown_seconds=1,
        max_cooldown_seconds=10,
    )
    for model in ("m1", "m2"):
        store.record_failure(
            "nvidia_api",
            model,
            ProviderHTTPError(
                "unauthorized",
                context=_ctx(provider="nvidia_api", model=model),
                status_code=401,
            ),
        )
    snapshot = store.provider_snapshot("nvidia_api")
    assert snapshot.state == "unavailable"
    assert snapshot.routable_model_count == 0
    assert not snapshot.routable
    assert store.available_providers(("nvidia_api",)) == ()
