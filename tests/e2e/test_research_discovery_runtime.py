import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

from aurora.orchestration.factory import build_director_runtime

class DiscoveryHandler(BaseHTTPRequestHandler):
    queries: list[str] = []

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self.__class__.queries.append(query)
            payload = {
                "results": [
                    {
                        "url": f"http://127.0.0.1:{self.server.server_port}/source",
                        "title": "Controlled research procedure",
                    }
                ]
            }
            body = json.dumps(payload).encode("utf-8")
        elif parsed.path == "/source":
            body = (
                b"- Configure the governed research procedure before execution.\n"
                b"- Verify the resulting evidence against the recorded source snapshot.\n"
                b"Preserve provenance and record the validation result.\n"
            )
        else:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json" if parsed.path == "/search" else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def test_director_runtime_uses_http_research_discovery_end_to_end(tmp_path: Path, monkeypatch):
    DiscoveryHandler.queries.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiscoveryHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    network_policy = tmp_path / "network-policy.json"
    network_policy.write_text(
        json.dumps(
            {
                "default": "deny",
                "protocols": ["http"],
                "allowed_hosts": ["127.0.0.1"],
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

    monkeypatch.setenv(
        "AURORA_RESEARCH_DISCOVERY_ENDPOINT",
        f"http://127.0.0.1:{server.server_port}/search",
    )
    try:
        runtime = build_director_runtime(
            inference_policy_path="config/inference-policy.json",
            autonomy_policy_path="config/autonomy-policy.json",
            registry_path="config/tool-registry.json",
            network_policy_path=network_policy,
            workspace_root=tmp_path / "workspace",
            egress_proxy_host="127.0.0.1",
            egress_proxy_port=3128,
            worker_id="research-discovery-e2e",
            event_consumer_id="research-discovery-e2e-events",
            worker_concurrency=1,
        )

        prepared = runtime.acquire_specialist(
            mission_id="mission-http-discovery",
            gap_skill_id="research-http-e2e",
            reason="discover a governed procedure through the configured search provider",
            required_capabilities=(),
        )

        assert DiscoveryHandler.queries
        assert "Discover reliable procedures for specialist skill research-http-e2e" in DiscoveryHandler.queries[0]
        assert prepared.evidence.state == "awaiting_approval"
        assert prepared.evidence.validation_returncode == 0
        assert len(prepared.evidence.source_ids) == 1
        assert prepared.evidence.construction_evidence_state == "complete"

        research_store = runtime.autonomous_specialist_acquisition.research.store
        source = research_store.source(prepared.evidence.source_ids[0])
        assert source is not None
        assert source.url.endswith("/source")
        assert "Configure the governed research procedure" in source.content_text
        assert "Verify the resulting evidence" in source.content_text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
