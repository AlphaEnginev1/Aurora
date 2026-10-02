import pytest

from aurora.orchestration import factory


def test_default_director_provider_is_router(monkeypatch):
    monkeypatch.delenv("AURORA_DIRECTOR_PROVIDER", raising=False)
    assert factory._build_director_provider() is None


def test_gemini_can_be_selected_as_director_provider(monkeypatch):
    class FakeGeminiProvider:
        provider_id = "gemini"

        def __init__(self, **kwargs):
            self.config = kwargs

    monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", "gemini")
    monkeypatch.setattr(factory, "GeminiDirectorProvider", FakeGeminiProvider)
    provider = factory._build_director_provider()
    assert provider.provider_id == "gemini"


def test_unknown_director_provider_fails_closed(monkeypatch):
    monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", "unknown")
    with pytest.raises(RuntimeError, match="unsupported AURORA_DIRECTOR_PROVIDER"):
        factory._build_director_provider()
