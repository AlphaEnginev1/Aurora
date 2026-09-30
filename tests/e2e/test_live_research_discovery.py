import json
import os
from pathlib import Path
from urllib.parse import urlparse

from aurora.autonomy.internet import InternetGateway, InternetPolicy
from aurora.orchestration.factory import build_director_runtime
from aurora.research.models import ResearchRequest
from aurora.skills.acquisition import SkillGap


def test_live_research_discovery_uses_real_search_provider_and_persists_source(
    tmp_path: Path, monkeypatch
):
    import pytest

    # Live gate remains explicitly opt-in; this branch is used only for provider validation.
    if os.environ.get("AURORA_LIVE_RESEARCH_GATE") != "1":
        pytest.skip("live research discovery gate is opt-in")

    endpoint = os.environ.get(
        "AURORA_RESEARCH_DISCOVERY_ENDPOINT",
        "https://searx.party/search?format=json",
    )
    provider_host = urlparse(endpoint).hostname
    assert provider_host, "live search endpoint must contain a hostname"
    monkeypatch.setenv("AURORA_RESEARCH_DISCOVERY_ENDPOINT", endpoint)

    network_policy = tmp_path / "network-policy.json"
    network_policy.write_text(
        json.dumps(
            {
                "default": "deny",
                "protocols": ["https"],
                "allowed_hosts": [provider_host],
                "browser_isolation_required": True,
                "sandbox_network": "broker_only",
                "os_isolation": {
                    "backend": "bubblewrap",
                    "required": True,
                    "unshare_net": True,
                    "direct_network": False,
                },
            }
        ),
        encoding="utf-8",
    )

    runtime = build_director_runtime(
        inference_policy_path="config/inference-policy.json",
        autonomy_policy_path="config/autonomy-policy.json",
        registry_path="config/tool-registry.json",
        network_policy_path=network_policy,
        workspace_root=tmp_path / "workspace",
        egress_proxy_host="127.0.0.1",
        egress_proxy_port=3128,
        worker_id="live-research-discovery",
        worker_concurrency=1,
    )

    specialist = runtime.autonomous_specialist_acquisition
    assert specialist is not None
    discovery = specialist.discovery
    assert discovery is not None

    research_service = specialist.research
    assert research_service.discovery is not None
    assert research_service.discovery.endpoint == endpoint

    gap = SkillGap(
        skill_id="research-live-provider-proof",
        mission_id="live-research-provider-proof",
        reason="SearXNG",
        required_capabilities=(),
    )

    urls = discovery.source_discovery(gap)
    assert urls, "live search provider returned no candidate sources"

    first_source_host = urlparse(urls[0]).hostname
    assert first_source_host, "live search provider returned an invalid source URL"
    assert urlparse(urls[0]).scheme in {"http", "https"}

    research_service.gateway = InternetGateway(
        InternetPolicy(
            protocols=frozenset({"https"}),
            allowed_hosts=frozenset({provider_host, first_source_host}),
        )
    )

    sources = research_service.retrieve(
        ResearchRequest(
            request_id="live-research-provider-proof",
            objective=gap.reason,
            required_sources=(urls[0],),
            max_sources=1,
        )
    )
    assert len(sources) == 1
    source = sources[0]
    assert source.status_code == 200
    assert source.content_sha256
    assert source.content_text.strip()

    restored = research_service.store.source(source.source_id)
    assert restored is not None
    assert restored.content_sha256 == source.content_sha256
    assert restored.url == source.url
