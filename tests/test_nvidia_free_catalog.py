import pytest

from aurora.inference.providers.nvidia_free_catalog import (
    NVIDIAFreeEndpointCatalogProvider,
)


def test_nvidia_free_catalog_extracts_model_ids_from_free_cards(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"""
            <article>
              <a href="/meta">Meta</a>
              <span>Downloadable Free Endpoint</span>
              <a href="/meta/example-model">example-model</a>
            </article>
            <article>
              <span>Downloadable</span>
              <a href="/nvidia/paid-model">paid-model</a>
            </article>
            """

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.urlopen",
        lambda _request, timeout: FakeResponse(),
    )
    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.time.sleep",
        lambda _seconds: None,
    )

    ids = NVIDIAFreeEndpointCatalogProvider(
        url="https://build.nvidia.com/models?pageSize=96",
    ).discover()

    assert ids == ("meta/example-model",)


def test_nvidia_free_catalog_scopes_badges_to_their_own_cards_and_handles_void_tags(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"""
            <main>
              <input name="filter">
              <article>
                <a href="/deepseek-ai">DeepSeek AI</a>
                <div class="badges"><span>Downloadable Free Endpoint</span></div>
                <h3><a href="/deepseek-ai/deepseek-v4.1-flash">deepseek-v4.1-flash</a></h3>
                <a href="/deepseek-ai/deepseek-v4.1-flash">Build</a>
              </article>
              <article>
                <a href="/meta">Meta</a>
                <span>Downloadable</span>
                <h3><a href="/meta/paid-model">paid-model</a></h3>
              </article>
              <article>
                <a href="/z-ai">Z.ai</a>
                <span>Free Endpoint</span>
                <h3><a href="/z-ai/glm-5-3">glm-5-3</a></h3>
              </article>
            </main>
            """

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.urlopen",
        lambda _request, timeout: FakeResponse(),
    )
    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.time.sleep",
        lambda _seconds: None,
    )

    ids = NVIDIAFreeEndpointCatalogProvider().discover()

    assert ids == ("deepseek-ai/deepseek-v4.1-flash", "z-ai/glm-5-3")
    assert "meta/paid-model" not in ids


def test_nvidia_free_catalog_retries_an_incomplete_page_before_failing(monkeypatch):
    responses = [b"<html><body>Temporary incomplete page</body></html>", b"""
        <article>
          <span>Free Endpoint</span>
          <a href="/poolside/laguna-xs-2.1">laguna-xs-2.1</a>
        </article>
    """]

    class FakeResponse:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.body

    calls = {"count": 0}

    def fake_urlopen(_request, timeout):
        calls["count"] += 1
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.urlopen",
        fake_urlopen,
    )
    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.time.sleep",
        lambda _seconds: None,
    )

    ids = NVIDIAFreeEndpointCatalogProvider(max_attempts=3).discover()

    assert ids == ("poolside/laguna-xs-2.1",)
    assert calls["count"] == 2


def test_nvidia_free_catalog_fails_closed_when_no_free_model_is_parsed(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"<html><body>No model cards</body></html>"

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.urlopen",
        lambda _request, timeout: FakeResponse(),
    )
    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.time.sleep",
        lambda _seconds: None,
    )

    with pytest.raises(Exception, match="contained no model IDs"):
        NVIDIAFreeEndpointCatalogProvider(max_attempts=3).discover()
