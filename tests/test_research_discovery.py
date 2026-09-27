from aurora.autonomy.internet import InternetPolicy
from aurora.research.discovery import HttpSearchProvider
from aurora.research.models import ResearchRequest
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore


class FakeGateway:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self.payload


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


def test_research_service_fails_closed_without_discovery_provider(tmp_path):
    gateway = FakeGateway(b"unused")
    service = ResearchService(gateway, ResearchStore(tmp_path / "research.db"))
    request = ResearchRequest(request_id="r1", objective="discover evidence")
    try:
        service.discover(request)
    except RuntimeError as exc:
        assert "provider is not configured" in str(exc)
    else:
        raise AssertionError("research discovery unexpectedly bypassed the provider boundary")
