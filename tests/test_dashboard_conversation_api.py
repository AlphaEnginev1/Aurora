from pathlib import Path
import json
from threading import Thread
from urllib.request import Request, urlopen

from aurora.dashboard.conversation_api import DashboardConversationAPI
from aurora.dashboard.http_server import create_dashboard_server
from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
from aurora.inference.router import ModelRouter
from aurora.context import AuroraContextPlane, ContextGate
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director_bootstrap import (
    DirectorBootstrap,
    build_context_knowledge_adapter,
    build_context_mailbox_adapter,
    build_context_runtime_adapter,
)
from aurora.orchestration.director_conversation import DirectorConversationService
from aurora.orchestration.director_decision_knowledge_store import DirectorDecisionKnowledgeStore
from aurora.orchestration.director_decision_performance import DirectorDecisionPerformanceStore
from aurora.orchestration.director_decision_policy_active_store import DirectorDecisionPolicyActiveStore
from aurora.orchestration.director_decision_policy_contract_store import DirectorDecisionPolicyContractStore
from aurora.orchestration.director_decision_policy_proposal_store import DirectorDecisionPolicyProposalStore
from aurora.projects import ProjectRegistry, ProjectSpec, ProjectStatus
from aurora.research.store import ResearchStore
from aurora.skills import SkillRegistry
from aurora.orchestration.task_store import DurableTaskStore


class FakeProvider:
    provider_id = "fake"

    def generate(self, request, *, selected_model, execution_id):
        return InferenceResponse(
            model=selected_model,
            provider="fake",
            content="Respuesta HTTP de Aurora.",
            request_id=request.request_id,
            execution_id=execution_id,
            selected_model=selected_model,
            provider_model=selected_model,
            reported_model=selected_model,
        )


def _conversation(tmp_path: Path, project_registry: ProjectRegistry | None = None):
    task_db = tmp_path / "tasks.db"
    projects = project_registry or ProjectRegistry({})
    tasks = DurableTaskStore(task_db)
    research = ResearchStore(tmp_path / "research.db")
    organization = OrganizationRegistry(
        agents={"support": AgentSpec("support", "support", "operations", "Support", frozenset({"reasoning"}), ("fake/director",), ())},
        departments={"operations": DepartmentSpec("operations", "Support", ("support",), ("fake/director",), frozenset({"reasoning"}))},
    )
    model = ModelSpec("fake/director", "fake", "https://example.invalid", capabilities=("reasoning",))
    plane = AuroraContextPlane(
        projects, organization, SkillRegistry({}), (model,),
        runtime_context=build_context_runtime_adapter(tasks),
        mailbox_context=build_context_mailbox_adapter(tasks),
        knowledge_context=build_context_knowledge_adapter(research),
    )
    gate = ContextGate(plane)
    router = ModelRouter(
        models=[model],
        context_gate=gate,
        enforce_context=True,
        provider_registry=ProviderRegistry((
            ProviderSpec("fake", "Fake", ("chat", "reasoning"), True, lambda spec: FakeProvider()),
        )),
    )
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "version": 1, "default_capability": "reasoning",
        "providers": {"fake": {"enabled": True}},
        "models": [{"id": "fake/director", "provider": "fake", "enabled": True, "capabilities": ["reasoning"]}],
        "routing": {}
    }), encoding="utf-8")
    bootstrap = DirectorBootstrap(
        context_gate=gate, model_policy_path=policy,
        decision_performance_store=DirectorDecisionPerformanceStore(task_db),
        active_policy_store=DirectorDecisionPolicyActiveStore(task_db),
        policy_proposal_store=DirectorDecisionPolicyProposalStore(task_db),
        policy_contract_store=DirectorDecisionPolicyContractStore(task_db),
        decision_knowledge_store=DirectorDecisionKnowledgeStore(task_db),
    )
    service = DirectorConversationService(
        router=router,
        bootstrap=bootstrap,
        db_path=tmp_path / "conversation.db",
        project_registry=projects,
    )
    return service


def test_http_conversation_round_trip(tmp_path):
    conversation = _conversation(tmp_path)
    api = DashboardConversationAPI(conversation)

    class Gateway:
        def snapshot(self):
            return {"read_only": True}
        def project(self, project_id):
            return {"project": {"project_id": project_id}}
        def agent(self, role):
            return {"identity": {"role": role}}

    server = create_dashboard_server(
        dashboard_gateway=Gateway(),
        conversation_api=api,
        host="127.0.0.1",
        port=0,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_address[1])

    try:
        session_request = Request(
            base + "/api/conversation/session",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(session_request, timeout=5) as response:
            session = json.loads(response.read().decode("utf-8"))
        assert response.status == 201
        assert session["state"] == "active"

        body = json.dumps({
            "user_input": "Hola por HTTP",
            "request_id": "http-round-trip-stable-request-001",
        }).encode("utf-8")
        turn_request = Request(
            base + "/api/conversation/" + session["session_id"] + "/turn",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(turn_request, timeout=5) as response:
            turn = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert turn["response_text"] == "Respuesta HTTP de Aurora."
        assert turn["session"]["session_id"] == session["session_id"]
        assert turn["turn"]["correlation_id"].startswith("corr-")
        assert turn["state"] == "completed"
        assert turn["model"] == "fake/director"
        assert turn["provider"] == "fake"
        assert turn["execution_id"]
        assert turn["context_manifest_id"]
        assert turn["presentation_hints"]["avatar_state"] == "SPEAKING"
        assert turn["turn"]["model"] == turn["model"]
        assert turn["turn"]["provider"] == turn["provider"]
        assert turn["turn"]["execution_id"] == turn["execution_id"]

        # A client resend after losing the first HTTP response uses the same
        # request_id; it must replay the persisted turn instead of inserting
        # another user turn or calling the provider again.
        retry_request = Request(
            base + "/api/conversation/" + session["session_id"] + "/turn",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(retry_request, timeout=5) as response:
            retried_turn = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert retried_turn["turn"]["turn_id"] == turn["turn"]["turn_id"]
        assert retried_turn["correlation_id"] == turn["correlation_id"]
        assert retried_turn["response_text"] == turn["response_text"]

        with urlopen(base + "/api/conversation/" + session["session_id"] + "/turns", timeout=5) as response:
            history = json.loads(response.read().decode("utf-8"))
        assert [item["role"] for item in history["turns"]] == ["user", "assistant"]
        assert history["turns"][1]["content"] == "Respuesta HTTP de Aurora."
        assert history["turns"][1]["execution_id"] == turn["execution_id"]

        # Stop the original HTTP server, reconstruct the service from the same
        # SQLite path, and read history through a fresh HTTP server instance.
        server.shutdown()
        server.server_close()
        recovered = DashboardConversationAPI(_conversation(tmp_path))
        restarted = create_dashboard_server(
            dashboard_gateway=Gateway(),
            conversation_api=recovered,
            host="127.0.0.1",
            port=0,
        )
        restarted_thread = Thread(target=restarted.serve_forever, daemon=True)
        restarted_thread.start()
        restarted_base = "http://127.0.0.1:" + str(restarted.server_address[1])
        try:
            with urlopen(
                restarted_base + "/api/conversation/" + session["session_id"] + "/turns",
                timeout=5,
            ) as response:
                recovered_history = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert [item["role"] for item in recovered_history["turns"]] == ["user", "assistant"]
            assert recovered_history["turns"][1]["content"] == "Respuesta HTTP de Aurora."
            assert recovered_history["turns"][1]["correlation_id"] == turn["correlation_id"]
            assert recovered_history["turns"][1]["execution_id"] == turn["execution_id"]
        finally:
            restarted.shutdown()
            restarted.server_close()
    finally:
        if server is not None:
            server.server_close()


def test_dashboard_server_rejects_non_loopback_without_token():
    import pytest
    from aurora.dashboard.http_server import DashboardHTTPError

    with pytest.raises(DashboardHTTPError, match="non-loopback"):
        create_dashboard_server(
            dashboard_gateway=object(),
            conversation_api=object(),
            host="0.0.0.0",
            port=8787,
            ui_root=Path(__file__).parents[1] / "aurora" / "dashboard" / "ui",
        )


def test_http_serves_canonical_portrait_asset(tmp_path):
    """Prove the real dashboard HTTP server serves the canonical JPEG asset."""
    conversation = _conversation(tmp_path)
    api = DashboardConversationAPI(conversation)

    class Gateway:
        def snapshot(self):
            return {"read_only": True}

        def project(self, project_id):
            return {"project": {"project_id": project_id}}

        def agent(self, role):
            return {"identity": {"role": role}}

    ui_root = Path(__file__).parents[1] / "aurora" / "dashboard" / "ui"
    asset_path = ui_root / "aurora-central-avatar.jpg"
    asset = asset_path.read_bytes()

    assert asset_path.is_file()
    assert asset.startswith(b"\xff\xd8\xff")
    assert len(asset) > 3

    server = create_dashboard_server(
        dashboard_gateway=Gateway(),
        conversation_api=api,
        host="127.0.0.1",
        port=0,
        ui_root=ui_root,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_address[1])

    try:
        with urlopen(base + "/aurora-central-avatar.jpg", timeout=5) as response:
            served = response.read()
            content_type = response.headers.get("Content-Type")

        assert response.status == 200
        assert content_type == "image/jpeg"
        assert served == asset
    finally:
        server.shutdown()
        server.server_close()



def test_http_conversation_turn_accepts_and_returns_selected_project_id(tmp_path):
    projects = ProjectRegistry({
        "project-http": ProjectSpec(
            project_id="project-http",
            name="HTTP Project",
            objective="Owns HTTP-scoped conversation",
            status=ProjectStatus.ACTIVE,
            repository="AlphaEnginev1/http-project",
            workspace=str(tmp_path / "projects" / "http-project"),
            current_state="Registered test project",
            updated_at="2026-10-16T00:00:00Z",
        ),
    })
    conversation = _conversation(tmp_path, projects)
    api = DashboardConversationAPI(conversation)

    class Gateway:
        def snapshot(self):
            return {"read_only": True}
        def project(self, project_id):
            return {"project": {"project_id": project_id}}
        def agent(self, role):
            return {"identity": {"role": role}}

    server = create_dashboard_server(
        dashboard_gateway=Gateway(),
        conversation_api=api,
        host="127.0.0.1",
        port=0,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_address[1])
    try:
        with urlopen(
            Request(
                base + "/api/conversation/session",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=5,
        ) as response:
            session = json.loads(response.read().decode("utf-8"))
        body = json.dumps({
            "user_input": "Hola, contexto del proyecto HTTP.",
            "request_id": "http-selected-project-001",
            "project_id": "project-http",
        }).encode("utf-8")
        with urlopen(
            Request(
                base + "/api/conversation/" + session["session_id"] + "/turn",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=5,
        ) as response:
            turn = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert turn["project_id"] == "project-http"
        assert turn["state"] == "completed"
        assert turn["turn"]["model"] == turn["model"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
