import io
import json

import pytest

from aurora.inference.errors import ProviderHTTPError, ProviderResponseError
from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.providers.anthropic_api import AnthropicAPIProvider
from aurora.inference.providers.openai_api import OpenAIAPIProvider


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_openai_adapter_preserves_identity_usage_and_request_shape(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data)
        return _Response({
            "model": "gpt-served",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAIAPIProvider(
        ModelSpec("gpt-test", "openai", "https://api.openai.test/v1"),
        api_key="SECRET-OPENAI",
    )
    request = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-test",
    )
    response = provider.generate(request, selected_model="gpt-test", execution_id="exec-openai")

    assert response.provider == "openai"
    assert response.content == "hello"
    assert response.execution_id == "exec-openai"
    assert response.reported_model == "gpt-served"
    assert response.usage.total_tokens == 10
    assert captured["body"]["model"] == "gpt-test"
    assert captured["body"]["messages"][0]["content"] == "hello"
    assert captured["headers"]["Authorization"] == "Bearer SECRET-OPENAI"


def test_openai_http_failure_is_typed_without_secret(monkeypatch):
    error = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
        "https://api.openai.test/v1/chat/completions", 401, "unauthorized", {}, None
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: (_ for _ in ()).throw(error))
    provider = OpenAIAPIProvider(
        ModelSpec("gpt-test", "openai", "https://api.openai.test/v1"),
        api_key="SECRET-OPENAI",
    )
    with pytest.raises(ProviderHTTPError) as caught:
        provider.generate(InferenceRequest(messages=[]), execution_id="exec-openai-error")
    assert caught.value.status_code == 401
    assert "SECRET-OPENAI" not in str(caught.value)


def test_openai_http_failure_includes_sanitized_provider_diagnostic(monkeypatch):
    payload = {
        "error": {
            "message": (
                "The model is blocked at the project level. "
                "credential echo SECRET-OPENAI and gsk_123456789abcdefgh"
            ),
            "type": "permissions_error",
            "code": "model_permission_blocked_project",
        }
    }
    error = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
        "https://api.groq.test/openai/v1/chat/completions",
        403,
        "forbidden",
        {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: (_ for _ in ()).throw(error))
    provider = OpenAIAPIProvider(
        ModelSpec("openai/gpt-oss-120b", "groq", "https://api.groq.test/openai/v1"),
        api_key="SECRET-OPENAI",
        provider_id="groq",
    )

    with pytest.raises(ProviderHTTPError) as caught:
        provider.generate(
            InferenceRequest(messages=[{"role": "user", "content": "hello"}], model="openai/gpt-oss-120b")
        )

    diagnostic = str(caught.value)
    assert caught.value.status_code == 403
    assert "model_permission_blocked_project" in diagnostic
    assert "permissions_error" in diagnostic
    assert "blocked at the project level" in diagnostic
    assert "SECRET-OPENAI" not in diagnostic
    assert "gsk_123456789abcdefgh" not in diagnostic
    assert "[REDACTED]" in diagnostic


def test_anthropic_adapter_translates_system_and_image_messages(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data)
        return _Response({
            "model": "claude-served",
            "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}],
            "usage": {"input_tokens": 7, "output_tokens": 5},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = AnthropicAPIProvider(
        ModelSpec("claude-test", "anthropic", "https://api.anthropic.test/v1"),
        api_key="SECRET-ANTHROPIC",
    )
    request = InferenceRequest(
        messages=[
            {"role": "system", "content": "You are Aurora."},
            {"role": "user", "content": [
                {"modality": "text", "text": "Inspect"},
                {"modality": "image", "mime_type": "image/png", "data": "ZmFrZQ=="},
            ]},
        ],
        model="claude-test",
        max_tokens=200,
    )
    response = provider.generate(request, selected_model="claude-test", execution_id="exec-anthropic")

    assert response.provider == "anthropic"
    assert response.content == "hello world"
    assert response.usage.total_tokens == 12
    headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert headers["x-api-key"] == "SECRET-ANTHROPIC"
    assert headers["anthropic-version"] == "2023-06-01"
    assert captured["body"]["system"] == "You are Aurora."
    image = captured["body"]["messages"][0]["content"][1]
    assert image["type"] == "image"
    assert image["source"]["media_type"] == "image/png"
    assert image["source"]["data"] == "ZmFrZQ=="


@pytest.mark.parametrize("payload", [{}, {"content": []}, {"content": [{"type": "image"}]}])
def test_anthropic_invalid_responses_are_typed(monkeypatch, payload):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: _Response(payload))
    provider = AnthropicAPIProvider(
        ModelSpec("claude-test", "anthropic", "https://api.anthropic.test/v1"),
        api_key="SECRET-ANTHROPIC",
    )
    with pytest.raises(ProviderResponseError):
        provider.generate(InferenceRequest(messages=[]))


def test_anthropic_http_failure_is_typed(monkeypatch):
    error = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
        "https://api.anthropic.test/v1/messages", 429, "rate limited", {}, None
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: (_ for _ in ()).throw(error))
    provider = AnthropicAPIProvider(
        ModelSpec("claude-test", "anthropic", "https://api.anthropic.test/v1"),
        api_key="SECRET-ANTHROPIC",
    )
    with pytest.raises(ProviderHTTPError) as caught:
        provider.generate(InferenceRequest(messages=[]))
    assert caught.value.status_code == 429
