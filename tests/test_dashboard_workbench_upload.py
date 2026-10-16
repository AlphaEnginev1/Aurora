from pathlib import Path
import json
import threading
import urllib.request

from aurora.autonomy.internet import InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.dashboard.http_server import create_dashboard_server
from aurora.workbench import UniversalWorkbench
from aurora.projects import ProjectRegistry, ProjectSpec, ProjectStatus
from aurora.workbench.models import WorkbenchResult
from aurora.workbench.session import WorkSessionStore


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


class InternetGateway:
    timeout = 1.0

    def __init__(self):
        self.policy = InternetPolicy(frozenset({"https"}), frozenset({"example.com", "api.github.com"}))

    def authorize(self, _url: str) -> None:
        return None


def test_dashboard_upload_retry_uses_one_durable_artifact_action(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sessions = WorkSessionStore(runtime / "work-sessions.db")
    workbench = UniversalWorkbench(
        repository_root=tmp_path,
        runtime_root=runtime,
        gateway=InternetGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=sessions,
        audit_path=runtime / "audit.jsonl",
        configured_roots=(),
    )
    executed = []

    def intake(request):
        executed.append((tuple(request.arguments), request.idempotency_key))
        return WorkbenchResult(
            capability=request.capability,
            operation="artifact_intake",
            status="succeeded",
            output={
                "artifact_id": "artifact-stable-upload",
                "filename": "sample.txt",
                "sha256": request.options["upload_sha256"],
            },
            artifact_id="artifact-stable-upload",
            provenance="controlled-upload",
        )

    monkeypatch.setattr(workbench, "_artifact_intake", intake)
    server = create_dashboard_server(
        dashboard_gateway=Gateway(),
        conversation_api=Conversation(),
        host="127.0.0.1",
        port=0,
        workbench=workbench,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request_id = "upload-request-stable-001"
    body = b"same upload bytes"
    url = f"http://127.0.0.1:{server.server_port}/api/dashboard/artifacts"

    def send(data: bytes):
        return urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "text/plain",
                "X-Aurora-Filename": "sample.txt",
                "X-Aurora-Request-ID": request_id,
            },
        )

    try:
        with urllib.request.urlopen(send(body), timeout=5) as response:
            first = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(send(body), timeout=5) as response:
            replay = json.loads(response.read().decode("utf-8"))

        assert first["mission_id"] == replay["mission_id"]
        assert first["artifact"]["artifact_id"] == replay["artifact"]["artifact_id"]
        assert len(executed) == 1
        assert executed[0][1] == f"dashboard-upload:{request_id}"
        uploaded = Path(executed[0][0][0])
        assert uploaded.read_bytes() == body

        # The same request ID cannot be reused to overwrite the accepted upload.
        try:
            urllib.request.urlopen(send(b"different upload bytes"), timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code in {400, 409}
        else:
            raise AssertionError("request-id upload was rebound to different bytes")
        assert uploaded.read_bytes() == body
        assert len(executed) == 1
    finally:
        server.shutdown()
        server.server_close()



def test_project_scoped_dashboard_upload_stays_inside_project_workspace_and_is_idempotent(
    tmp_path: Path, monkeypatch
):
    runtime = tmp_path / "runtime"
    workspace = runtime / "workspace"
    alpha_root = workspace / "projects" / "alpha"
    beta_root = workspace / "projects" / "beta"
    registry = ProjectRegistry({
        "project-alpha": ProjectSpec(
            project_id="project-alpha",
            name="Project Alpha",
            objective="Alpha-only uploaded documents",
            status=ProjectStatus.ACTIVE,
            repository="AlphaEnginev1/alpha",
            workspace="projects/alpha",
            current_state="Active alpha project",
            updated_at="2026-10-16T00:00:00Z",
        ),
        "project-beta": ProjectSpec(
            project_id="project-beta",
            name="Project Beta",
            objective="Beta-only uploaded documents",
            status=ProjectStatus.ACTIVE,
            repository="AlphaEnginev1/beta",
            workspace="projects/beta",
            current_state="Active beta project",
            updated_at="2026-10-16T00:00:00Z",
        ),
    })
    sessions = WorkSessionStore(runtime / "work-sessions.db")
    workbench = UniversalWorkbench(
        repository_root=tmp_path,
        runtime_root=runtime,
        workspace_root=workspace,
        gateway=InternetGateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
        session_store=sessions,
        audit_path=runtime / "audit.jsonl",
        project_registry=registry,
    )
    executed = []

    def intake(request):
        uploaded = Path(request.arguments[0]).resolve()
        executed.append({
            "path": uploaded,
            "project_id": request.options.get("project_id"),
            "request_idempotency_key": request.idempotency_key,
            "bytes": uploaded.read_bytes(),
        })
        artifact_id = f"artifact-{request.options.get('project_id')}"
        return WorkbenchResult(
            capability=request.capability,
            operation="artifact_intake",
            status="succeeded",
            output={
                "artifact_id": artifact_id,
                "filename": request.options["filename"],
                "sha256": request.options["upload_sha256"],
                "path": str(uploaded),
            },
            artifact_id=artifact_id,
            provenance=str(uploaded),
        )

    monkeypatch.setattr(workbench, "_artifact_intake", intake)
    server = create_dashboard_server(
        dashboard_gateway=Gateway(),
        conversation_api=Conversation(),
        host="127.0.0.1",
        port=0,
        workbench=workbench,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/dashboard/artifacts"
    body = b"scope-bound attachment"
    request_id = "scoped-upload-request-001"

    def send(project_id):
        headers = {
            "Content-Type": "text/plain",
            "X-Aurora-Filename": "notes.txt",
            "X-Aurora-Request-ID": request_id,
        }
        if project_id is not None:
            headers["X-Aurora-Project-ID"] = project_id
        return urllib.request.Request(url, data=body, method="POST", headers=headers)

    try:
        with urllib.request.urlopen(send("project-alpha"), timeout=5) as response:
            alpha = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(send("project-beta"), timeout=5) as response:
            beta = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(send("project-alpha"), timeout=5) as response:
            replay_alpha = json.loads(response.read().decode("utf-8"))

        assert alpha["project_id"] == "project-alpha"
        assert beta["project_id"] == "project-beta"
        assert replay_alpha["mission_id"] == alpha["mission_id"]
        assert replay_alpha["artifact"]["artifact_id"] == alpha["artifact"]["artifact_id"]
        assert len(executed) == 2
        alpha_entry = next(item for item in executed if item["project_id"] == "project-alpha")
        beta_entry = next(item for item in executed if item["project_id"] == "project-beta")
        assert alpha_entry["path"].is_relative_to(alpha_root.resolve())
        assert beta_entry["path"].is_relative_to(beta_root.resolve())
        assert alpha_entry["path"] != beta_entry["path"]
        assert alpha_entry["request_idempotency_key"] != beta_entry["request_idempotency_key"]
        assert alpha_entry["bytes"] == beta_entry["bytes"] == body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_upload_rejects_unregistered_project_scope(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    workbench = UniversalWorkbench(
        repository_root=tmp_path,
        runtime_root=runtime,
        gateway=InternetGateway(),
        project_registry=ProjectRegistry({}),
    )
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
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/dashboard/artifacts",
            data=b"not allowed",
            method="POST",
            headers={
                "Content-Type": "text/plain",
                "X-Aurora-Filename": "private.txt",
                "X-Aurora-Project-ID": "not-registered",
            },
        )
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("upload accepted an unregistered project")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
