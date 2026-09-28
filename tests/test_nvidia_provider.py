import json
import socket
import urllib.error

import pytest

from aurora.inference.errors import (
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.providers.nvidia_api import NVIDIAAPIProvider


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _provider():
    return NVIDIAAPIProvider(
        ModelSpec(
            id="nvidia/model-a",
            provider="nvidia_api",
            endpoint="https://example.invalid/v1",
        ),
        api_key="SECRET-NVIDIA-KEY",
    )


def test_success_preserves_identity_usage_and_latency(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: _Response(
            {
                "model": "nvidia/model-a-served",
                "choices": [{"message": {"content": "hello"}}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 11,
                    "total_tokens": 18,
                },
            }
        ),
    )
    request = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="requested-model",
    )
    response = _provider().generate(
        request,
        selected_model="nvidia/model-a",
        execution_id="exec-9b-1",
    )

    assert response.content == "hello"
    assert response.request_id == request.request_id
    assert response.execution_id == "exec-9b-1"
    assert response.requested_model == "requested-model"
    assert response.selected_model == "nvidia/model-a"
    assert response.provider_model == "nvidia/model-a"
    assert response.reported_model == "nvidia/model-a-served"
    assert response.usage.total_tokens == 18
    assert response.telemetry is not None
    assert response.telemetry.duration_ms >= 0
    assert response.telemetry.success is True


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (socket.timeout("secret"), ProviderTimeoutError),
        (TimeoutError("secret"), ProviderTimeoutError),
        (urllib.error.URLError("secret"), ProviderTransportError),
    ],
)
def test_transport_failures_are_typed_and_do_not_leak_exception_text(
    monkeypatch, exc, expected
):
    def fail(req, timeout):
        raise exc

    monkeypatch.setattr("urllib.request.urlopen", fail)
    request = InferenceRequest(messages=[])

    with pytest.raises(expected) as caught:
        _provider().generate(request, execution_id="exec-error")

    error = caught.value
    assert "SECRET-NVIDIA-KEY" not in str(error)
    assert "secret" not in str(error)
    assert error.context.request_id == request.request_id
    assert error.context.execution_id == "exec-error"


def test_http_failure_does_not_capture_response_body_or_credentials(monkeypatch):
    body = "SECRET-NVIDIA-KEY should never enter the exception"
    http_error = urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions",
        401,
        "unauthorized",
        {},
        None,
    )

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: (_ for _ in ()).throw(http_error))
    with pytest.raises(ProviderHTTPError) as caught:
        _provider().generate(InferenceRequest(messages=[]), execution_id="exec-http")

    assert "SECRET-NVIDIA-KEY" not in str(caught.value)
    assert body not in str(caught.value)
    assert caught.value.context.execution_id == "exec-http"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": 123}}]},
        {"choices": [{"message": {"content": "ok"}}], "usage": []},
        {"choices": [{"message": {"content": "ok"}}], "model": 123},
    ],
)
def test_invalid_provider_responses_are_typed(monkeypatch, payload):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _Response(payload))
    with pytest.raises(ProviderResponseError):
        _provider().generate(InferenceRequest(messages=[]))


def test_invalid_json_is_typed(monkeypatch):
    class BadResponse(_Response):
        def read(self):
            return b"not-json"

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: BadResponse({}))
    with pytest.raises(ProviderResponseError) as caught:
        _provider().generate(InferenceRequest(messages=[]))
    assert caught.value.code == "provider_invalid_response"
