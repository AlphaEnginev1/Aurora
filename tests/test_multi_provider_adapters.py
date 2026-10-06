import json
from pathlib import Path

from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.providers.anthropic_api import AnthropicAPIProvider, AnthropicModelDiscoveryProvider
from aurora.inference.providers.openai_api import OpenAIAPIProvider, OpenAIModelDiscoveryProvider
from aurora.inference.providers.registry import default_provider_registry


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.payload


def test_default_registry_contains_multi_provider_adapters():
    registry = default_provider_registry(
        nvidia_api_key="n",
        openai_api_key="o",
        anthropic_api_key="a",
    )
    assert registry.ids == ("anthropic", "nvidia_api", "openai")


def test_openai_adapter_preserves_neutral_contract(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: FakeResponse(
            {
                "model": "gpt-test",
                "output_text": "hello",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            }
        ),
    )
    provider = OpenAIAPIProvider(
        ModelSpec("gpt-test", "openai", "https://example.invalid/v1", ("chat", "reasoning")),
        api_key="key",
    )
    response = provider.generate(
        InferenceRequest(messages=[{"role": "user", "content": "hi"}]),
        selected_model="gpt-test",
        execution_id="exec",
    )
    assert response.provider == "openai"
    assert response.model == "gpt-test"
    assert response.content == "hello"
    assert response.usage.total_tokens == 5


def test_anthropic_adapter_preserves_neutral_contract(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: FakeResponse(
            {
                "model": "claude-test",
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 2, "output_tokens": 4},
            }
        ),
    )
    provider = AnthropicAPIProvider(
        ModelSpec("claude-test", "anthropic", "https://example.invalid/v1", ("chat", "reasoning")),
        api_key="key",
    )
    response = provider.generate(
        InferenceRequest(messages=[{"role": "user", "content": "hi"}]),
        selected_model="claude-test",
        execution_id="exec",
    )
    assert response.provider == "anthropic"
    assert response.model == "claude-test"
    assert response.content == "hello"
    assert response.usage.total_tokens == 6


def test_multi_provider_discovery_stages_models_without_activation(monkeypatch):
    responses = iter(
        [
            FakeResponse({"data": [{"id": "gpt-a", "owned_by": "openai"}]}),
            FakeResponse({"data": [{"id": "claude-a", "display_name": "Claude A", "max_input_tokens": 100000}]}),
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: next(responses))
    openai = OpenAIModelDiscoveryProvider(api_key="key", base_url="https://example.invalid/v1")
    anthropic = AnthropicModelDiscoveryProvider(api_key="key", base_url="https://example.invalid/v1")
    discovered_openai = tuple(openai.discover())
    discovered_anthropic = tuple(anthropic.discover())
    assert discovered_openai[0].model.id == "gpt-a"
    assert discovered_anthropic[0].model.id == "claude-a"
    assert discovered_openai[0].state == "discovered"
    assert discovered_anthropic[0].state == "discovered"
