from pathlib import Path
import json
from threading import Thread
from urllib.request import Request, urlopen

from aurora.dashboard.http_server import create_dashboard_server

ROOT = Path(__file__).parents[1] / "aurora" / "dashboard" / "ui"


class SettingsStub:
    def public(self):
        return {
            "director": {"provider": "router", "model": "nvidia/nemotron-3-ultra-550b-a55b", "endpoint": "https://example.invalid"},
            "providers": {
                "nvidia_api": {"enabled": True, "api_key_present": False, "base_url": "https://example.invalid"},
            },
            "runtime": {"director_provider": "inference_router"},
        }

    def save_and_apply(self, payload):
        return self.public()

    def test_provider(self, provider):
        return {"provider": provider, "ok": True}


class Gateway:
    def snapshot(self):
        return {"director": {"readiness": "READY"}}
    def project(self, project_id):
        return {"project_id": project_id}
    def agent(self, role):
        return {"role": role}


def test_dashboard_settings_http_contract():
    settings = SettingsStub()
    server = create_dashboard_server(
        dashboard_gateway=Gateway(),
        conversation_api=object(),
        provider_settings=settings,
        host="127.0.0.1",
        port=0,
        ui_root=ROOT,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_address[1])
    try:
        with urlopen(base + "/api/dashboard/settings", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert data["runtime"]["director_provider"] == "inference_router"
        encoded = json.dumps(data)
        assert "nvidia-secret" not in encoded

        request = Request(
            base + "/api/dashboard/settings",
            data=json.dumps({"director": {"provider": "router"}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            saved = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert saved["runtime"]["director_provider"] == "inference_router"

        request = Request(
            base + "/api/dashboard/settings/test",
            data=b'{"provider":"nvidia_api"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            tested = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert tested == {"ok": True, "provider": "nvidia_api"}
    finally:
        server.shutdown()
        server.server_close()
