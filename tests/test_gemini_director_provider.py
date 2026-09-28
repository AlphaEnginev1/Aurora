import json

import pytest

from aurora.orchestration.director_provider import DirectorToolSpec
from aurora.orchestration.gemini_provider import (
    GeminiDirectorConfig,
    GeminiDirectorError,
    GeminiDirectorProvider,
)


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_gemini_provider_translates_text_and_tool_calls(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHTTPResponse({
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "I will delegate this."},
                        {
                            "functionCall": {
                                "id": "call-7",
                                "name": "delegate_task",
                                "args": {
                                    "task_id": "research",
                                    "role": "researcher",
                                    "objective": "research evidence",
                                },
                            }
                        },
                    ]
                }
            }]
        })

    monkeypatch.setattr("aurora.orchestration.gemini_provider.urlopen", fake_urlopen)
    provider = GeminiDirectorProvider(
        GeminiDirectorConfig(
            api_key="test-key",
            model="gemini-3.7-flash",
            endpoint="https://example.test/v1beta",
            timeout_seconds=4,
        )
    )
    response = provider.generate(
        messages=[
            {"role": "system", "content": "You are Aurora's Director."},
            {"role": "user", "content": "research"},
        ],
        model=None,
        role="director_planner",
        tools=(
            DirectorToolSpec(
                name="delegate_task",
                description="delegate",
                parameters={
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
            ),
        ),
    )

    assert response.provider == "gemini"
    assert response.model == "gemini-3.7-flash"
    assert response.content == "I will delegate this."
    assert response.tool_calls[0].call_id == "call-7"
    assert response.tool_calls[0].name == "delegate_task"
    body = json.loads(captured["request"].data)
    assert body["systemInstruction"]["parts"][0]["text"] == "You are Aurora's Director."
    declaration = body["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "delegate_task"
    assert "additionalProperties" not in declaration["parameters"]
    assert captured["request"].get_header("X-goog-api-key") == "test-key"


def test_gemini_provider_never_executes_returned_tool_call(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeHTTPResponse({
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "delegate_task",
                            "args": {"task_id": "t", "role": "r", "objective": "o"},
                        }
                    }]
                }
            }]
        })

    monkeypatch.setattr("aurora.orchestration.gemini_provider.urlopen", fake_urlopen)
    provider = GeminiDirectorProvider(api_key="test-key")
    response = provider.generate(
        messages=[{"role": "user", "content": "do work"}],
        model=None,
        role="director_planner",
    )
    assert response.tool_calls[0].name == "delegate_task"


def test_gemini_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiDirectorError, match="GEMINI_API_KEY"):
        GeminiDirectorProvider()



def test_gemini_provider_translates_nullable_and_array_schema(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return FakeHTTPResponse({
            "candidates": [{
                "content": {"parts": [{"text": "ok"}]}
            }]
        })

    monkeypatch.setattr("aurora.orchestration.gemini_provider.urlopen", fake_urlopen)
    provider = GeminiDirectorProvider(api_key="test-key")
    provider.generate(
        messages=[{"role": "user", "content": "plan"}],
        model=None,
        role="director_planner",
        tools=(
            DirectorToolSpec(
                name="delegate_task",
                description="delegate",
                parameters={
                    "type": "object",
                    "properties": {
                        "capability": {"type": ["string", "null"]},
                        "args": {"type": "array", "items": {"type": "string"}},
                    },
                },
            ),
        ),
    )
    body = json.loads(captured["request"].data)
    parameters = body["tools"][0]["functionDeclarations"][0]["parameters"]
    assert parameters["properties"]["capability"] == {
        "type": "string",
        "nullable": True,
    }
    assert parameters["properties"]["args"] == {
        "type": "array",
        "items": {"type": "string"},
    }


def test_gemini_provider_retries_transient_http_errors(monkeypatch):
    calls = {"count": 0}

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] < 2:
            from urllib.error import HTTPError
            raise HTTPError(
                request.full_url,
                503,
                "temporary",
                {},
                None,
            )
        return FakeHTTPResponse({
            "candidates": [{
                "content": {"parts": [{"text": "recovered"}]}
            }]
        })

    monkeypatch.setattr("aurora.orchestration.gemini_provider.urlopen", fake_urlopen)
    provider = GeminiDirectorProvider(
        GeminiDirectorConfig(api_key="test-key", max_retries=1)
    )
    response = provider.generate(
        messages=[{"role": "user", "content": "recover"}],
        model=None,
        role="director_planner",
    )
    assert response.content == "recovered"
    assert calls["count"] == 2
