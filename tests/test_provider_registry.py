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



def test_default_registry_can_opt_into_free_api_providers():
    registry = default_provider_registry(
        nvidia_api_key="nvidia-test",
        include_free=True,
        free_api_keys={
            "groq": "groq-test",
            "gemini": "gemini-test",
            "cloudflare": "cloudflare-test",
            "openrouter": "openrouter-test",
        },
    )
    assert {"nvidia_api", "groq", "gemini", "cloudflare", "mistral", "openrouter"} <= set(registry.ids)
    provider = registry.create(
        ModelSpec(
            "openai/gpt-oss-120b",
            "groq",
            "https://api.groq.com/openai/v1",
            pricing_tier="free",
        )
    )
    assert provider.provider_id == "groq"
    assert provider.api_key == "groq-test"


def test_free_api_provider_does_not_fall_back_to_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-borrowed")
    registry = default_provider_registry(
        include_free=True,
        free_api_keys={"groq": ""},
    )
    provider = registry.create(
        ModelSpec(
            "openai/gpt-oss-20b",
            "groq",
            "https://api.groq.com/openai/v1",
            pricing_tier="free",
        )
    )
    assert provider.api_key == ""
    assert provider.health() is False
