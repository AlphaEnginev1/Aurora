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

    ids = NVIDIAFreeEndpointCatalogProvider(
        url="https://build.nvidia.com/models?pageSize=96",
    ).discover()

    assert ids == ("meta/example-model",)


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

    try:
        NVIDIAFreeEndpointCatalogProvider().discover()
    except Exception as exc:
        assert "contained no model IDs" in str(exc)
    else:
        raise AssertionError("catalog parser must fail closed when no free IDs are found")
