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
from aurora.projects import ProjectRegistry
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


def _conversation(tmp_path: Path):
    task_db = tmp_path / "tasks.db"
    tasks = DurableTaskStore(task_db)
    research = ResearchStore(tmp_path / "research.db")
    organization = OrganizationRegistry(
        agents={"support": AgentSpec("support", "support", "operations", "Support", frozenset({"reasoning"}), ("fake/director",), ())},
        departments={"operations": DepartmentSpec("operations", "Support", ("support",), ("fake/director",), frozenset({"reasoning"}))},
    )
    model = ModelSpec("fake/director", "fake", "https://example.invalid", capabilities=("reasoning",))
    plane = AuroraContextPlane(
        ProjectRegistry({}), organization, SkillRegistry({}), (model,),
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
    service = DirectorConversationService(router=router, bootstrap=bootstrap, db_path=tmp_path / "conversation.db")
    return service


def test_http_conversation_round_trip(tmp_path):
    conversation = _conversation(tmp_path)
    api = DashboardConversationAPI(conversation)
    dashboard = create_dashboard_server(
        dashboard_gateway=__import__("aurora.dashboard.gateway", fromlist=["DashboardGateway"]).DashboardGateway.__new__(
            __import__("aurora.dashboard.gateway", fromlist=["DashboardGateway"]).DashboardGateway
        ),
        conversation_api=api,
    )
    dashboard.server_close()
