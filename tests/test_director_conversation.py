from pathlib import Path
import json

import pytest


seen = []

from aurora.context import AuroraContextPlane, ContextGate
from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
from aurora.inference.router import ModelRouter
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director_bootstrap import (
    DirectorBootstrap,
    build_context_knowledge_adapter,
    build_context_mailbox_adapter,
    build_context_runtime_adapter,
)
from aurora.orchestration.director_conversation import (
    DirectorConversationError,
    DirectorConversationService,
)
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.director_decision_knowledge_store import DirectorDecisionKnowledgeStore
from aurora.orchestration.director_decision_performance import DirectorDecisionPerformanceStore
from aurora.orchestration.director_decision_policy_active_store import DirectorDecisionPolicyActiveStore
from aurora.orchestration.director_decision_policy_contract_store import DirectorDecisionPolicyContractStore
from aurora.orchestration.director_decision_policy_proposal_store import DirectorDecisionPolicyProposalStore
from aurora.organization import OrganizationRegistry
from aurora.projects import ProjectRegistry
from aurora.research.store import ResearchStore
from aurora.skills import SkillRegistry
from aurora.orchestration.task_store import DurableTaskStore


class FakeProvider:
    provider_id = "fake"

    def __init__(self, seen):
        self.seen = seen

    def generate(self, request, *, selected_model, execution_id):
        self.seen.append((request, selected_model, execution_id))
        return InferenceResponse(
            model=selected_model,
            provider="fake",
            content="Respuesta de Aurora.",
            request_id=request.request_id,
            execution_id=execution_id,
            selected_model=selected_model,
            provider_model=selected_model,
            reported_model=selected_model,
        )


def _service(tmp_path: Path):
    task_db = tmp_path / "tasks.db"
    task_store = DurableTaskStore(task_db)
    research_store = ResearchStore(tmp_path / "research.db")
    organization = OrganizationRegistry(
        agents={
            "support": AgentSpec(
                "support",
                "support",
                "operations",
                "Support",
                frozenset({"reasoning"}),
                ("fake/director",),
                (),
            )
        },
        departments={
            "operations": DepartmentSpec(
                "operations",
                "Support",
                ("support",),
                ("fake/director",),
                frozenset({"reasoning"}),
            )
        },
    )
    model = ModelSpec(
        "fake/director",
        "fake",
        "https://example.invalid",
        capabilities=("reasoning",),
        healthy=True,
        available=True,
    )
    rogue_model = ModelSpec(
        "fake/rogue",
        "fake",
        "https://example.invalid",
        capabilities=("reasoning",),
        quality_score=1.0,
        healthy=True,
        available=True,
    )
    plane = AuroraContextPlane(
        project_registry=ProjectRegistry({}),
        organization=organization,
        skill_registry=SkillRegistry({}),
        models=(model, rogue_model),
        runtime_context=build_context_runtime_adapter(task_store),
        mailbox_context=build_context_mailbox_adapter(task_store),
        knowledge_context=build_context_knowledge_adapter(research_store),
    )
    gate = ContextGate(plane)
    router = ModelRouter(
        models=[model, rogue_model],
        context_gate=gate,
        enforce_context=True,
        provider_registry=ProviderRegistry(
            (
                ProviderSpec(
                    "fake",
                    "Fake",
                    ("chat", "reasoning"),
                    True,
                    lambda spec: FakeProvider(seen),
                ),
            )
        ),
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_capability": "reasoning",
                "providers": {"fake": {"enabled": True}},
                "models": [
                    {
                        "id": "fake/director",
                        "provider": "fake",
                        "enabled": True,
                        "capabilities": ["reasoning"],
                        "context_window": 8192,
                    }
                ],
                "routing": {},
            }
        ),
        encoding="utf-8",
    )
    bootstrap = DirectorBootstrap(
        context_gate=gate,
        model_policy_path=policy_path,
        decision_performance_store=DirectorDecisionPerformanceStore(task_db),
        active_policy_store=DirectorDecisionPolicyActiveStore(task_db),
        policy_proposal_store=DirectorDecisionPolicyProposalStore(task_db),
        policy_contract_store=DirectorDecisionPolicyContractStore(task_db),
        decision_knowledge_store=DirectorDecisionKnowledgeStore(task_db),
    )
    return (
        DirectorConversationService(
            router=router,
            bootstrap=bootstrap,
            db_path=tmp_path / "conversation.db",
        ),
        seen,
    )


def test_conversation_bootstraps_context_and_persists_turns(tmp_path):
    service, seen = _service(tmp_path)
    session = service.create_session()

    response = service.send_message(
        session_id=session.session_id,
        user_input="Hola Aurora",
    )

    assert response.response_text == "Respuesta de Aurora."
    assert response.provider == "fake"
    assert response.model == "fake/director"
    assert response.context_manifest_id.startswith("ctx-")
    assert response.presentation_hints["avatar_state"] == "SPEAKING"
    request = seen[-1][0]
    assert request.metadata["role"] == "director_conversation"
    assert request.metadata["conversation_session_id"] == session.session_id
    assert request.metadata["aurora_context_verified"] is True
    assert any("source=aurora.identity" in item["content"] for item in request.messages)

    turns = service.list_turns(session.session_id, limit=10)
    assert [item.role for item in turns] == ["user", "assistant"]
    assert turns[-1].execution_id == response.execution_id


def test_second_turn_receives_prior_conversation_without_bypassing_context(tmp_path):
    service, seen = _service(tmp_path)
    session = service.create_session()
    service.send_message(session_id=session.session_id, user_input="Primero")
    service.send_message(session_id=session.session_id, user_input="Segundo")

    request = seen[-1][0]
    assert [item["content"] for item in request.messages[-3:]] == [
        "Primero",
        "Respuesta de Aurora.",
        "Segundo",
    ]
    assert request.metadata["aurora_context_verified"] is True


def test_requested_model_must_be_enabled_by_director_policy(tmp_path):
    service, _ = _service(tmp_path)
    session = service.create_session()

    with pytest.raises(
        DirectorConversationError,
        match="not enabled by policy",
    ):
        service.send_message(
            session_id=session.session_id,
            user_input="test",
            model="unapproved/model",
        )


def test_closed_session_rejects_new_turn(tmp_path):
    service, _ = _service(tmp_path)
    session = service.create_session()
    service.close_session(session.session_id)

    with pytest.raises(DirectorConversationError, match="not active"):
        service.send_message(session_id=session.session_id, user_input="test")


def test_failed_provider_is_persisted_without_exposing_credentials(tmp_path):
    service, seen = _service(tmp_path)
    session = service.create_session()

    class FailingProvider:
        provider_id = "fake"

        def generate(self, request, *, selected_model, execution_id):
            raise RuntimeError("provider failure")

    service.router.provider_registry = ProviderRegistry(
        (
            ProviderSpec(
                "fake",
                "Fake",
                ("chat", "reasoning"),
                True,
                lambda spec: FailingProvider(),
            ),
        )
    )

    with pytest.raises(DirectorConversationError, match="inference failed"):
        service.send_message(session_id=session.session_id, user_input="test")

    failed = service.list_turns(session.session_id, limit=10)
    assert failed[-1].state == "failed"
    assert failed[-1].error_code == "runtimeerror"

def test_gemini_style_provider_response_contract(tmp_path):
    service, _ = _service(tmp_path)

    class GeminiStyleProvider:
        provider_id = "gemini"

        def generate(self, *, messages, model, role, tools=()):
            return DirectorModelResponse(
                content="Respuesta Gemini.",
                model=model or "gemini-3.7-flash",
                provider="gemini",
                request_id="request-gemini",
                execution_id="execution-gemini",
                tool_calls=(),
            )

    service.set_provider(GeminiStyleProvider())
    session = service.create_session()

    response = service.send_message(
        session_id=session.session_id,
        user_input="Hola Aurora",
    )

    assert response.response_text == "Respuesta Gemini."
    assert response.provider == "gemini"
    assert response.model == "gemini-3.7-flash"
    assert response.execution_id == "execution-gemini"
