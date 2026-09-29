import json
from pathlib import Path
from urllib.parse import urlparse

from aurora.autonomy.internet import InternetPolicy
from aurora.orchestration.factory import build_director_runtime
from aurora.research.models import ResearchRequest
from aurora.skills.acquisition import SkillGap


def test_live_research_discovery_uses_real_search_provider_and_persists_source(tmp_path: Path, monkeypatch):
    endpoint = "https://searx.oloke.xyz/search?format=json"
    monkeypatch.setenv("AURORA_RESEARCH_DISCOVERY_ENDPOINT", endpoint)

    network_policy = tmp_path / "network-policy.json"
    network_policy.write_text(
        json.dumps(
            {
                "default": "deny",
                "protocols": ["https"],
                "allowed_hosts": ["searx.oloke.xyz"],
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
        reason="site:searx.oloke.xyz SearXNG search service",
        required_capabilities=(),
    )

    urls = discovery.source_discovery(gap)
    assert urls, "live search provider returned no candidate sources"
    assert all(urlparse(url).hostname == "searx.oloke.xyz" for url in urls)

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
