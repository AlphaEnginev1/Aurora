from pathlib import Path

from aurora.research.discovery import HttpSearchProvider
from aurora.research.models import ResearchRequest
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore


class FakeGateway:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.urls: list[str] = []

    def get(self, url: str) -> bytes:
        self.urls.append(url)
        return self.payload


class FakeDiscoveryProvider:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def search(self, objective: str, *, limit: int):
        self.calls.append((objective, limit))
        return (type("Result", (), {"url": "https://example.com/a"})(),)


def test_http_search_provider_discovers_unique_urls():
    gateway = FakeGateway(
        b'{"results":[{"url":"https://example.com/a","title":"A"},'
        b'{"url":"https://example.com/a","title":"duplicate"},'
        b'{"url":"https://example.com/b","snippet":"B"}]}'
    )
    provider = HttpSearchProvider(gateway, "https://search.example/search")

    results = provider.search("aurora research", limit=5)

    assert [item.url for item in results] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert "q=aurora+research" in gateway.urls[0]


def test_research_service_fails_closed_without_discovery_provider(tmp_path: Path):
    service = ResearchService(FakeGateway(b"unused"), ResearchStore(tmp_path / "research.db"))

    try:
        service.discover(
            ResearchRequest(request_id="r1", objective="discover evidence")
        )
    except RuntimeError as exc:
        assert "provider is not configured" in str(exc)
    else:
        raise AssertionError("research discovery unexpectedly bypassed the provider boundary")


def test_research_service_uses_configured_discovery_provider(tmp_path: Path):
    provider = FakeDiscoveryProvider()
    service = ResearchService(
        FakeGateway(b"unused"),
        ResearchStore(tmp_path / "research.db"),
        discovery=provider,
    )
    request = ResearchRequest(
        request_id="r2",
        objective="discover evidence",
        max_sources=3,
    )

    assert service.discover(request) == ("https://example.com/a",)
    assert provider.calls == [("discover evidence", 3)]
