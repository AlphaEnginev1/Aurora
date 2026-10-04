from pathlib import Path
import json
import threading
import urllib.request

from aurora.dashboard.http_server import create_dashboard_server
from aurora.workbench.models import WorkbenchResult


class Gateway:
    def snapshot(self):
        return {"director": {"readiness": "READY"}, "generated_at": "now"}

    def project(self, _project_id):
        return {}

    def agent(self, _role):
        return {}


class Conversation:
    def create_session(self, *, session_id=None, principal="dashboard"):
        return {"session_id": session_id or "s1", "principal": principal}

    def get_turns(self, *, session_id, principal):
        return {"session_id": session_id, "turns": [], "principal": principal}

    def send_turn(self, *, session_id, user_input, model, principal):
        return {"session_id": session_id, "response": user_input, "model": model, "principal": principal}


class Workbench:
    def __init__(self, tmp_path):
        self.runtime_root = tmp_path
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return WorkbenchResult(
            capability=request.capability,
            operation="intake",
            status="succeeded",
            output={"artifact_id": "abc123", "filename": "sample.txt"},
            artifact_id="abc123",
            provenance=str(request.arguments[0]),
        )


def test_dashboard_artifact_upload(tmp_path: Path):
    workbench = Workbench(tmp_path)
    server = create_dashboard_server(
        dashboard_gateway=Gateway(),
        conversation_api=Conversation(),
        host="127.0.0.1",
        port=0,
        workbench=workbench,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/dashboard/artifacts"
        request = urllib.request.Request(
            url,
            data=b"hello Aurora",
            method="POST",
            headers={
                "Content-Type": "text/plain",
                "X-Aurora-Filename": "sample.txt",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 201
        assert payload["artifact"]["artifact_id"] == "abc123"
        assert len(workbench.calls) == 1
        assert workbench.calls[0].capability == "workbench:artifact:intake"
        uploaded = Path(workbench.calls[0].arguments[0])
        assert uploaded.is_file()
        assert uploaded.read_bytes() == b"hello Aurora"
    finally:
        server.shutdown()
        server.server_close()
