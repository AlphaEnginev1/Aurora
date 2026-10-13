import pytest

from aurora.inference.models import InferenceRequest, InferenceResponse, ModelSpec
from aurora.inference.providers.registry import (
    ProviderRegistry,
    ProviderRegistryError,
    ProviderSpec,
    default_provider_registry,
)
from aurora.inference.router import ModelRouter, ModelRoutingError


class FakeProvider:
    provider_id = "fake"

    def __init__(self, model: ModelSpec):
        self.model = model
        self.calls = []

    def generate(self, request, *, selected_model: str, execution_id: str):
        self.calls.append((request, selected_model, execution_id))
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


def test_registry_rejects_duplicate_provider_ids():
    registry = ProviderRegistry()
    spec = ProviderSpec("fake", "Fake", ("chat",), True, lambda model: FakeProvider(model))
    registry.register(spec)

    with pytest.raises(ProviderRegistryError, match="duplicate provider registration"):
        registry.register(spec)


def test_registry_rejects_unknown_provider():
    registry = ProviderRegistry()

    with pytest.raises(ProviderRegistryError, match="unknown inference provider"):
        registry.resolve("missing")


def test_registry_catalog_exposes_provider_metadata_without_factory():
    registry = ProviderRegistry(
        (
            ProviderSpec(
                "fake",
                "Fake Provider",
                ("reasoning", "chat"),
                True,
                lambda model: FakeProvider(model),
            ),
        )
    )

    assert registry.ids == ("fake",)
    assert registry.catalog == (
        {
            "provider_id": "fake",
            "name": "Fake Provider",
            "enabled": True,
            "capabilities": ("chat", "reasoning"),
            "discovery_supported": False,
        },
    )


def test_router_uses_registered_provider_instead_of_vendor_branch():
    created = []

    def factory(model):
        provider = FakeProvider(model)
        created.append(provider)
        return provider

    registry = ProviderRegistry(
        (
            ProviderSpec(
                "fake",
                "Fake Provider",
                ("reasoning",),
                True,
                factory,
            ),
        )
    )
    router = ModelRouter(
        models=[
            ModelSpec(
                "fake/model",
                "fake",
                "https://example.invalid",
                capabilities=("reasoning",),
            )
        ],
        provider_registry=registry,
    )

    response = router.generate(
        InferenceRequest(
            messages=[{"role": "user", "content": "test"}],
        ),
        capability="reasoning",
    )

    assert response.provider == "fake"
    assert response.model == "fake/model"
    assert len(created) == 1
    assert created[0].calls[0][1] == "fake/model"


def test_router_rejects_model_with_unregistered_provider():
    router = ModelRouter(
        models=[
            ModelSpec(
                "unknown/model",
                "missing",
                "https://example.invalid",
                capabilities=("reasoning",),
            )
        ],
        provider_registry=ProviderRegistry(),
    )

    with pytest.raises(ModelRoutingError, match="unknown inference provider"):
        router.generate(
            InferenceRequest(
                messages=[{"role": "user", "content": "test"}],
            ),
            capability="reasoning",
        )


def test_default_registry_contains_current_nvidia_provider():
    registry = default_provider_registry(nvidia_api_key="test-key")
    assert "nvidia_api" in registry.ids
    assert any(
        item["provider_id"] == "nvidia_api"
        and item["name"] == "NVIDIA NIM API"
        for item in registry.catalog
    )


def test_default_registry_constructs_nvidia_discovery_adapter():
    registry = default_provider_registry(nvidia_api_key="test-key")
    discovery = registry.create_discovery("nvidia_api")
    assert discovery.provider_id == "nvidia_api"


def test_default_registry_can_opt_into_openrouter_without_openai_key():
    registry = default_provider_registry(
        nvidia_api_key="nvidia-test",
        openai_api_key="must-not-be-used",
        openrouter_api_key="openrouter-test",
        include_openrouter=True,
    )
    assert "openrouter" in registry.ids
    provider = registry.create(
        ModelSpec(
            "openrouter/free",
            "openrouter",
            "https://openrouter.ai/api/v1",
            pricing_tier="free",
        )
    )
    assert provider.provider_id == "openrouter"
    assert provider.api_key == "openrouter-test"


def test_openrouter_is_not_registered_without_explicit_opt_in():
    registry = default_provider_registry(openrouter_api_key="openrouter-test")
    assert "openrouter" not in registry.ids


def test_default_registry_can_opt_into_external_providers():
    registry = default_provider_registry(
        nvidia_api_key="test-key",
        openai_api_key="openai-test",
        anthropic_api_key="anthropic-test",
        include_external=True,
    )
    assert registry.ids == ("anthropic", "nvidia_api", "openai")

    openai = registry.create(
        ModelSpec("gpt-test", "openai", "https://api.openai.test/v1")
    )
    anthropic = registry.create(
        ModelSpec("claude-test", "anthropic", "https://api.anthropic.test/v1")
    )
    assert openai.provider_id == "openai"
    assert anthropic.provider_id == "anthropic"



def test_registry_applies_operator_endpoint_override_to_adapter_spec():
    registry = default_provider_registry(
        nvidia_api_key="nvidia-test",
        openai_api_key="openai-test",
        include_external=True,
        provider_endpoints={"openai": "https://openai-proxy.example/v1"},
    )
    provider = registry.create(
        ModelSpec("gpt-test", "openai", "https://api.openai.com/v1")
    )

    assert provider.spec.id == "gpt-test"
    assert provider.spec.endpoint == "https://openai-proxy.example/v1"


def test_registry_applies_operator_endpoint_override_to_nvidia_discovery():
    registry = default_provider_registry(
        nvidia_api_key="nvidia-test",
        provider_endpoints={"nvidia_api": "https://nvidia-proxy.example/v1"},
    )

    discovery = registry.create_discovery("nvidia_api")

    assert discovery.base_url == "https://nvidia-proxy.example/v1"
