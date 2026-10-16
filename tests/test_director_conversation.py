from pathlib import Path
import hashlib
import json
from types import SimpleNamespace

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
from aurora.orchestration.director import DirectorPlan
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine
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

def test_nvidia_style_provider_response_contract(tmp_path):
    service, _ = _service(tmp_path)

    class NVIDIAStyleProvider:
        provider_id = "nvidia_api"

        def generate(self, *, messages, model, role, tools=()):
            return DirectorModelResponse(
                content="Respuesta NVIDIA.",
                model=model or "nvidia/nemotron-3-ultra-550b-a55b",
                provider="nvidia_api",
                request_id="request-nvidia",
                execution_id="execution-nvidia",
                tool_calls=(),
            )

    service.set_provider(NVIDIAStyleProvider())
    session = service.create_session()
    response = service.send_message(session_id=session.session_id, user_input="Hola Aurora")

    assert response.response_text == "Respuesta NVIDIA."
    assert response.provider == "nvidia_api"
    assert response.model == "nvidia/nemotron-3-ultra-550b-a55b"
    assert response.execution_id == "execution-nvidia"

@pytest.mark.parametrize(
    "prompt",
    [
        "Audita toda la cadena de ejecucion y corrige los fallos",
        "Revisa todos los enlaces de Aurora y arregla los que fallen",
        "Implement the durable order path and run the tests",
        "Please continue with the integration work",
    ],
)
def test_work_intent_does_not_require_the_word_mission(prompt):
    assert DirectorConversationService._requests_mission_execution(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "Que hace run_mission?",
        "Lee el archivo README.md",
        "List the files in the repository",
        "How does ModelRouter select a model?",
    ],
)
def test_information_questions_and_single_reads_do_not_start_a_mission(prompt):
    assert DirectorConversationService._requests_mission_execution(prompt) is False


@pytest.mark.parametrize("emit_tool_call", [True, False])
def test_default_router_provider_submits_one_durable_mission_and_returns_evidence(tmp_path, emit_tool_call):
    service, _ = _service(tmp_path)
    session = service.create_session()
    received_requests = []
    submitted = []

    class ToolCallingProvider:
        provider_id = "fake"

        def generate(self, request, *, selected_model, execution_id):
            received_requests.append(request)
            tool_names = request.metadata.get("director_tools", [])
            if "run_mission" in tool_names and emit_tool_call:
                content = json.dumps({
                    "tool_calls": [{
                        "name": "run_mission",
                        "arguments": {"objective": "audit Aurora links"},
                    }]
                })
            else:
                content = "Aurora has processed the durable mission receipt."
            return InferenceResponse(
                model=selected_model,
                provider="fake",
                content=content,
                request_id=request.request_id,
                execution_id=execution_id,
                selected_model=selected_model,
                provider_model=selected_model,
                reported_model=selected_model,
            )

    service.router.provider_registry = ProviderRegistry((
        ProviderSpec(
            "fake", "Fake", ("chat", "reasoning"), True,
            lambda spec: ToolCallingProvider(),
        ),
    ))

    mission_db = tmp_path / "durable-missions.db"
    mission_store = DurableTaskStore(mission_db)
    planner_calls = []

    class RecordingPlanner:
        def plan(self, *, objective, context=None, model=None):
            planner_calls.append({
                "objective": objective,
                "context": context,
                "model": model,
            })
            return DirectorPlan(
                objective=objective,
                tasks=(
                    DelegatedTask(
                        task_id="durable-ingress-task",
                        role="support",
                        objective="Perform the requested project audit",
                    ),
                ),
                model="fake/director",
                provider="fake",
                execution_id="planning-execution-123",
            )

    class NoExecutionOrchestration:
        def run_task(self, **_kwargs):
            raise AssertionError("submission test must not start worker execution")

        def run_capability_task(self, **_kwargs):
            raise AssertionError("submission test must not execute capabilities")

    durable_director = DurableDirectorService(
        planner=RecordingPlanner(),
        task_engine=TaskEngine(
            NoExecutionOrchestration(),
            tmp_path / "durable-workspace",
        ),
        store=mission_store,
    )

    def mission_runner(**kwargs):
        submitted.append(dict(kwargs))
        submission = durable_director.submit_mission(
            objective=kwargs["objective"],
            mission_id=kwargs["mission_id"],
            correlation_id=kwargs["correlation_id"],
        )
        # Mirror the receipt shape produced by the canonical runtime while
        # leaving workers stopped: this regression verifies durable ingress,
        # not downstream task completion.
        return SimpleNamespace(
            mission_id=submission.mission_id,
            submission=submission,
            objective=kwargs["objective"],
            status="running",
            tasks=(),
            events=(),
        )

    service.mission_runner = mission_runner
    response = service.send_message(
        session_id=session.session_id,
        user_input="Audita todos los enlaces de Aurora y corrige los que fallen",
        request_id="durable-tool-routing-regression-001",
    )

    assert len(submitted) == 1
    expected_objective = (
        "audit Aurora links"
        if emit_tool_call
        else "Audita todos los enlaces de Aurora y corrige los que fallen"
    )
    assert submitted[0]["objective"] == expected_objective
    assert submitted[0]["mission_id"].startswith("mission-conversation-turn-request-")
    assert submitted[0]["correlation_id"] == response.correlation_id
    assert response.state == "completed"
    assert "EVIDENCIA_AUTORITATIVA_DE_EJECUCION (JSON):" in response.response_text
    assert '"accepted": true' in response.response_text
    assert '"mission_id": "' + submitted[0]["mission_id"] + '"' in response.response_text
    assert received_requests[0].metadata["conversation_session_id"] == session.session_id
    assert received_requests[0].metadata["aurora_context_verified"] is True

    # The real durable director committed one task graph and one submission receipt.
    mission_id = submitted[0]["mission_id"]
    persisted_tasks = mission_store.list_tasks(mission_id)
    assert [item.task.task_id for item in persisted_tasks] == ["durable-ingress-task"]
    submission_events = [
        event for event in mission_store.events(mission_id)
        if event.event_type == "mission_submitted"
    ]
    assert len(submission_events) == 1
    assert submission_events[0].payload["correlation_id"] == response.correlation_id
    assert submission_events[0].payload["task_ids"] == ["durable-ingress-task"]
    assert len(planner_calls) == 1

    # A resend after losing the HTTP response replays the same turn/mission receipt.
    replay = service.send_message(
        session_id=session.session_id,
        user_input="Audita todos los enlaces de Aurora y corrige los que fallen",
        request_id="durable-tool-routing-regression-001",
    )
    assert replay.turn.turn_id == response.turn.turn_id
    assert replay.correlation_id == response.correlation_id
    assert len(submitted) == 1


def test_durable_mission_receipt_is_returned_when_summary_provider_fails(tmp_path):
    service, _seen = _service(tmp_path)
    session = service.create_session()
    generated_roles = []

    class FailingSummaryProvider:
        provider_id = "fake"

        def generate(
            self,
            *,
            messages,
            model,
            role,
            tools=(),
            metadata=None,
            capability="reasoning",
            preferred_models=(),
        ):
            generated_roles.append(role)
            if role == "director_conversation_result":
                raise RuntimeError("controlled summary-provider outage")
            return DirectorModelResponse(
                content="Aurora has accepted the request.",
                model=model or "fake/director",
                provider="fake",
                request_id="request-initial",
                execution_id="execution-initial",
                tool_calls=(),
            )

    service.set_provider(FailingSummaryProvider())
    submitted = []

    def durable_runner(**kwargs):
        submitted.append(dict(kwargs))
        mission_id = kwargs["mission_id"]
        correlation_id = kwargs["correlation_id"]
        submission = SimpleNamespace(
            mission_id=mission_id,
            plan=SimpleNamespace(
                objective=kwargs["objective"],
                tasks=(),
                model="fake/director",
                provider="fake",
                execution_id="planning-execution",
            ),
        )
        event = SimpleNamespace(
            event_id=1,
            event_type="mission_submitted",
            task_id=None,
            project_id=kwargs.get("project_id"),
            correlation_id=correlation_id,
            payload={
                "objective": kwargs["objective"],
                "project_id": kwargs.get("project_id"),
                "correlation_id": correlation_id,
                "task_ids": [],
            },
        )
        return SimpleNamespace(
            mission_id=mission_id,
            objective=kwargs["objective"],
            submission=submission,
            status=SimpleNamespace(value="running"),
            tasks=(),
            events=(event,),
        )

    service.mission_runner = durable_runner
    request_id = "summary-provider-failure-after-acceptance"
    response = service.send_message(
        session_id=session.session_id,
        user_input="Audita la cadena durable de Aurora",
        request_id=request_id,
    )

    assert generated_roles == ["director_conversation", "director_conversation_result"]
    assert len(submitted) == 1
    assert response.state == "completed"
    assert "EVIDENCIA_AUTORITATIVA_DE_EJECUCION (JSON):" in response.response_text
    assert '"accepted": true' in response.response_text
    assert submitted[0]["mission_id"] in response.response_text
    assert not any(turn.state == "failed" for turn in service.list_turns(session.session_id, limit=10))
    saved = service.send_message(
        session_id=session.session_id,
        user_input="Audita la cadena durable de Aurora",
        request_id=request_id,
    )
    assert saved.turn.turn_id == response.turn.turn_id
    assert len(submitted) == 1


def test_replaying_running_mission_turn_refreshes_status_without_reexecution(tmp_path):
    service, _seen = _service(tmp_path)
    session = service.create_session()
    provider_calls = []
    mission_calls = []
    status = {"value": "running"}

    class ConversationProvider:
        provider_id = "fake"

        def generate(
            self,
            *,
            messages,
            model,
            role,
            tools=(),
            metadata=None,
            capability="reasoning",
            preferred_models=(),
        ):
            provider_calls.append(role)
            return DirectorModelResponse(
                content=(
                    "The order has been accepted."
                    if role == "director_conversation"
                    else "Execution evidence is attached."
                ),
                model=model or "fake/director",
                provider="fake",
                request_id=f"provider-request-{len(provider_calls)}",
                execution_id=f"provider-execution-{len(provider_calls)}",
                tool_calls=(),
            )

    service.set_provider(ConversationProvider())

    def durable_result(mission_id, objective, current_status):
        submitted = SimpleNamespace(
            event_id=1,
            event_type="mission_submitted",
            task_id=None,
            project_id="project-a",
            correlation_id="corr-running-turn",
            payload={
                "objective": objective,
                "project_id": "project-a",
                "correlation_id": "corr-running-turn",
            },
        )
        events = [submitted]
        if current_status == "succeeded":
            events.append(SimpleNamespace(
                event_id=2,
                event_type="mission_succeeded",
                task_id=None,
                project_id="project-a",
                correlation_id="corr-running-turn",
                payload={"status": "succeeded", "project_id": "project-a"},
            ))
        return SimpleNamespace(
            mission_id=mission_id,
            objective=objective,
            submission=SimpleNamespace(
                mission_id=mission_id,
                plan=SimpleNamespace(
                    objective=objective,
                    tasks=(),
                    model="fake/director",
                    provider="fake",
                    execution_id="planning-execution",
                ),
            ),
            status=SimpleNamespace(value=current_status),
            tasks=(),
            events=tuple(events),
        )

    def runner(**kwargs):
        mission_calls.append(dict(kwargs))
        return durable_result(
            kwargs["mission_id"],
            kwargs["objective"],
            "running",
        )

    service.mission_runner = runner
    service.mission_status_reader = lambda mission_id: durable_result(
        mission_id,
        "Audita la orden durable de Aurora",
        status["value"],
    )

    request_id = "refresh-running-order-status-001"
    first = service.send_message(
        session_id=session.session_id,
        user_input="Audita la orden durable de Aurora",
        request_id=request_id,
    )

    assert first.state == "completed"
    assert first.execution_status == "running"
    assert '"status": "running"' in first.response_text
    assert len(mission_calls) == 1
    assert provider_calls == [
        "director_conversation",
        "director_conversation_result",
    ]

    status["value"] = "succeeded"
    replay = service.send_message(
        session_id=session.session_id,
        user_input="Audita la orden durable de Aurora",
        request_id=request_id,
    )

    assert replay.state == "completed"
    assert replay.execution_status == "succeeded"
    assert "Estado actualizado de la orden durable de Aurora." in replay.response_text
    assert '"status": "succeeded"' in replay.response_text
    assert replay.turn.turn_id == first.turn.turn_id
    assert replay.correlation_id == first.correlation_id
    assert len(mission_calls) == 1
    assert provider_calls == [
        "director_conversation",
        "director_conversation_result",
    ]


def test_mission_execution_requires_stable_request_id_before_persisting_order(
    tmp_path: Path,
):
    service, _seen = _service(tmp_path)
    session = service.create_session()
    calls = []
    service.mission_runner = lambda **kwargs: calls.append(kwargs)

    with pytest.raises(DirectorConversationError, match="request_id is required"):
        service.send_message(
            session_id=session.session_id,
            user_input="Audita la cadena durable de Aurora",
        )

    assert calls == []
    assert service.list_turns(session.session_id, limit=10) == ()


def test_provider_requested_mission_without_request_id_fails_before_runner_invocation(
    tmp_path: Path,
):
    service, _seen = _service(tmp_path)
    session = service.create_session()
    executed = []

    class RequestsMissionProvider:
        provider_id = "fake"

        def generate(
            self, *, messages, model, role, tools=(), metadata=None,
            capability="reasoning", preferred_models=(),
        ):
            return DirectorModelResponse(
                content="",
                model=model or "fake/director",
                provider="fake",
                request_id="provider-request",
                execution_id="provider-execution",
                tool_calls=(
                    DirectorToolCall(
                        call_id="call-1",
                        name="run_mission",
                        arguments={"objective": "investigate"},
                    ),
                ) if role == "director_conversation" else (),
            )

    service.set_provider(RequestsMissionProvider())
    service.mission_runner = lambda **kwargs: executed.append(kwargs)

    with pytest.raises(DirectorConversationError, match="request_id is required"):
        service.send_message(
            session_id=session.session_id,
            user_input="Hola Aurora",
        )

    assert executed == []


def test_replay_recovers_a_durable_turn_left_without_an_assistant_response(tmp_path):
    service, _ = _service(tmp_path)
    session = service.create_session()
    request_id = "recover-interrupted-turn-001"
    digest = hashlib.sha256(
        f"{session.session_id}\x00{request_id}".encode("utf-8")
    ).hexdigest()[:32]
    turn_id = f"turn-request-{digest}"
    correlation_id = f"corr-request-{digest}"
    service._insert_turn(
        turn_id=turn_id,
        session_id=session.session_id,
        sequence=1,
        role="user",
        content="Recupera esta orden interrumpida",
        state="completed",
        created_at="2026-10-16T00:00:00+00:00",
        correlation_id=correlation_id,
    )

    response = service.send_message(
        session_id=session.session_id,
        user_input="Recupera esta orden interrumpida",
        request_id=request_id,
    )

    assert response.state == "completed"
    assert response.correlation_id == correlation_id
    turns = service.list_turns(session.session_id, limit=10)
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].turn_id == turn_id
    assert turns[1].turn_id == f"{turn_id}-response"
