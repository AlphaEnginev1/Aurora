from pathlib import Path
import json

from aurora.autonomy.internet import InternetPolicy, InternetResponse
from aurora.autonomy.policy import Policy
from aurora.research.discovery import DiscoveredSource
from aurora.research.models import ResearchRequest
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore
from aurora.workbench import UniversalWorkbench, WorkbenchRequest
from aurora.workbench.session import WorkSessionStore


class FakeDiscovery:
    def __init__(self):
        self.calls = []

    def search(self, objective: str, *, limit: int):
        self.calls.append((objective, limit))
        return (
            DiscoveredSource("https://example.com/a", "A"),
            DiscoveredSource("https://example.com/b", "B"),
        )


class FakeGateway:
    timeout = 1.0

    def __init__(self):
        self.policy = InternetPolicy(
            frozenset({"https"}),
            frozenset({"example.com", "search.example"}),
        )
        self.urls = []

    def get(self, url: str) -> bytes:
        self.urls.append(url)
        return (
            b"<html><body>"
            b"<p>Aurora Workbench research evidence.</p>"
            b"<p>Claims remain pending until independent verification.</p>"
            b"</body></html>"
        )

    def get_with_metadata(self, url: str) -> InternetResponse:
        self.urls.append(url)
        return InternetResponse(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body=(
                b"<html><body>"
                b"<p>Aurora Workbench research evidence.</p>"
                b"<p>Claims remain pending until independent verification.</p>"
                b"</body></html>"
            ),
        )


def build_workbench(tmp_path: Path, discovery):
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    gateway = FakeGateway()
    research = ResearchService(
        gateway,
        ResearchStore(runtime / "research.db"),
        discovery=discovery,
    )
    return UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=gateway,
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=WorkSessionStore(runtime / "sessions.db"),
        audit_path=runtime / "audit.jsonl",
        research=research,
    ), discovery, gateway, runtime


def test_workbench_research_discovery_and_retrieval_are_durable(tmp_path: Path):
    wb, discovery, gateway, runtime = build_workbench(tmp_path, FakeDiscovery())

    discovered = wb.execute(
        WorkbenchRequest(
            "workbench:research:discover",
            "researcher",
            "mission-research",
            reason="Find authoritative evidence",
            arguments=(json.dumps({
                "request_id": "research-1",
                "objective": "Find authoritative evidence",
                "max_sources": 2,
            }),),
            correlation_id="corr-discover",
        )
    )

    assert discovered.status == "succeeded"
    assert discovered.output["urls"] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert discovery.calls == [("Find authoritative evidence", 2)]

    retrieved = wb.execute(
        WorkbenchRequest(
            "workbench:research:retrieve_extract",
            "researcher",
            "mission-research",
            reason="Retrieve and extract claims",
            arguments=(json.dumps({
                "request_id": "research-2",
                "objective": "Retrieve authoritative evidence",
                "required_sources": ["https://example.com/a"],
                "max_sources": 1,
            }),),
            correlation_id="corr-retrieve",
        )
    )

    assert retrieved.status == "succeeded"
    assert len(retrieved.output["sources"]) == 1
    assert retrieved.output["sources"][0]["content_sha256"]
    assert len(retrieved.output["claims"]) == 2
    assert all(item["state"] == "pending" for item in retrieved.output["claims"])

    session = wb.session_store.get("mission-research")
    assert "workbench:research:discover" in session.capability_ids
    assert "workbench:research:retrieve_extract" in session.capability_ids
    assert len(session.execution_receipts) == 2
    assert gateway.urls == ["https://example.com/a"]


def test_workbench_research_rejects_malformed_requests(tmp_path: Path):
    wb, _, _, _ = build_workbench(tmp_path, FakeDiscovery())

    try:
        wb.execute(
            WorkbenchRequest(
                "workbench:research:discover",
                "researcher",
                "mission-bad",
                arguments=("not-json",),
            )
        )
    except Exception as exc:
        assert "valid JSON" in str(exc)
    else:
        raise AssertionError("malformed research request must fail closed")


def test_workbench_census_distinguishes_research_presence(tmp_path: Path):
    wb, _, _, _ = build_workbench(tmp_path, FakeDiscovery())
    row = next(item for item in wb.census() if item.capability_id == "workbench:research")
    assert row.available is True
    assert row.authorized is True
    assert row.healthy is True
