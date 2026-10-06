import json

import pytest

from aurora.inference.models import InferenceRequest, InferenceResponse, ModelSpec
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
from aurora.inference.registry import ModelRegistry, ModelRegistryError
from aurora.inference.router import ModelRouter


class FakeProvider:
    provider_id = "fake"

    def __init__(self, model: ModelSpec):
        self.model = model

    def generate(self, request, *, selected_model, execution_id):
        return InferenceResponse(
            model=selected_model,
            provider=self.provider_id,
            content="ok",
            request_id=request.request_id,
            execution_id=execution_id,
            selected_model=selected_model,
            provider_model=selected_model,
            reported_model=selected_model,
        )


@pytest.fixture
def provider_registry():
    return ProviderRegistry(
        (
            ProviderSpec(
                "fake",
                "Fake Provider",
                ("chat", "reasoning"),
                True,
                lambda model: FakeProvider(model),
            ),
        )
    )


def _model(model_id="fake/model", provider="fake", *, enabled=True):
    return ModelSpec(
        id=model_id,
        provider=provider,
        endpoint="https://example.invalid",
        capabilities=("reasoning",),
        context_window=8192,
        enabled=enabled,
        specialties=("research",),
    )


def test_registry_rejects_duplicate_model_ids():
    registry = ModelRegistry([_model()])

    with pytest.raises(ModelRegistryError, match="duplicate model registration"):
        registry.register(_model())


def test_registry_rejects_unknown_model():
    registry = ModelRegistry()

    with pytest.raises(ModelRegistryError, match="unknown inference model"):
        registry.resolve("missing/model")


def test_registry_validates_provider_identity_when_provider_registry_is_supplied(
    provider_registry,
):
    with pytest.raises(ModelRegistryError, match="unknown inference provider"):
        ModelRegistry([_model("orphan/model", "missing")], provider_registry=provider_registry)


def test_registry_rejects_model_using_disabled_provider():
    registry = ProviderRegistry(
        (
            ProviderSpec(
                "fake",
                "Fake Provider",
                ("chat",),
                False,
                lambda model: FakeProvider(model),
            ),
        )
    )

    with pytest.raises(ModelRegistryError, match="disabled provider"):
        ModelRegistry([_model()], provider_registry=registry)


def test_registry_catalog_exposes_model_metadata_without_factories(
    provider_registry,
):
    registry = ModelRegistry([_model()], provider_registry=provider_registry)

    assert registry.ids == ("fake/model",)
    assert registry.catalog == (
        {
            "model_id": "fake/model",
            "provider": "fake",
            "endpoint": "https://example.invalid",
            "enabled": True,
            "healthy": True,
            "available": True,
            "capabilities": ("reasoning",),
            "specialties": ("research",),
            "context_window": 8192,
            "quality_score": 0.5,
            "latency_score": 0.5,
            "cost_score": 0.5,
            "pricing_tier": "unknown",
        },
    )


def test_registry_replace_changes_authoritative_model_snapshot(provider_registry):
    registry = ModelRegistry([_model()], provider_registry=provider_registry)
    replacement = _model(enabled=False)

    registry.replace(replacement)

    assert registry.resolve("fake/model").enabled is False
    assert registry.models == (replacement,)


def test_registry_from_file_preserves_model_policy_and_validates_provider(
    provider_registry,
    tmp_path,
):
    path = tmp_path / "inference-policy.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "models": [
                    {
                        "id": "fake/model",
                        "provider": "fake",
                        "endpoint": "https://example.invalid",
                        "capabilities": ["reasoning"],
                        "context_window": 8192,
                        "enabled": False,
                        "specialties": ["research"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    registry = ModelRegistry.from_file(path, provider_registry=provider_registry)

    assert registry.resolve("fake/model").enabled is False


def test_router_reads_current_model_registry_snapshot(provider_registry):
    first = _model("fake/first")
    second = _model("fake/second")
    registry = ModelRegistry([first], provider_registry=provider_registry)
    router = ModelRouter(
        models=list(registry.models),
        model_registry=registry,
        provider_registry=provider_registry,
    )

    assert router.available() == [first]

    registry.register(second)

    assert router.available() == [first, second]
    response = router.generate(
        InferenceRequest(messages=[{"role": "user", "content": "test"}]),
        capability="reasoning",
    )
    assert response.provider == "fake"
    assert response.model in {"fake/first", "fake/second"}


def test_model_registry_is_compatible_with_legacy_router_model_list():
    model = _model()
    router = ModelRouter(models=[model])

    assert router.available() == [model]
    assert router.select(
        InferenceRequest(messages=[{"role": "user", "content": "test"}]),
        capability="reasoning",
    ) == model
