from pathlib import Path
from types import SimpleNamespace

from aurora.orchestration.director_conversation import DirectorConversationService
from aurora.orchestration.director_provider import DirectorModelResponse, DirectorToolCall
from tests.test_director_conversation import _service as base_service


class ToolProvider:
    provider_id = "nvidia_api"

    def __init__(self):
        self.calls = []

    def generate(self, *, messages, model, role, tools=()):
        self.calls.append((role, tuple(tool.name for tool in tools)))
        if role == "director_conversation":
            return DirectorModelResponse(
                content="",
                model=model or "nvidia/nemotron-3-ultra-550b-a55b",
                provider="gemini",
                request_id="request-1",
                execution_id="execution-1",
                tool_calls=(
                    DirectorToolCall(
                        call_id="call-1",
                        name="run_mission",
                        arguments={"objective": "Inspect the project"},
                    ),
                ),
            )
        assert role == "director_conversation_result"
        return DirectorModelResponse(
            content="He inspeccionado el proyecto y la ejecución ha terminado correctamente.",
            model=model or "nvidia/nemotron-3-ultra-550b-a55b",
            provider="gemini",
            request_id="request-2",
            execution_id="execution-2",
        )


def test_conversation_can_execute_governed_mission(tmp_path: Path):
    service, _ = base_service(tmp_path)
    provider = ToolProvider()
    executed = []

    service.set_provider(provider)

    def runner(*, objective):
        executed.append(objective)
        task = SimpleNamespace(
            task=SimpleNamespace(task_id="task-1", role="engineer", capability="workbench:file:read"),
            status="succeeded",
            error=None,
        )
        return SimpleNamespace(objective=objective, tasks=(task,))

    service.mission_runner = runner
    session = service.create_session()

    response = service.send_message(
        session_id=session.session_id,
        user_input="Audita el proyecto y haz el trabajo necesario.",
    )

    assert executed == ["Inspect the project"]
    assert response.response_text.startswith("He inspeccionado")
    assert ("director_conversation", ("run_mission",)) in provider.calls
    assert ("director_conversation_result", ()) in provider.calls


def test_conversation_can_use_shared_workbench_directly(tmp_path: Path):
    service, _ = base_service(tmp_path)
    provider = ToolProvider()
    service.set_provider(provider)

    class FakeWorkbench:
        def __init__(self):
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                status="succeeded",
                capability=request.capability,
                operation="file_read",
                output="authoritative file evidence",
                artifact_id="artifact-123",
                provenance="workbench:file:read",
                receipt_id="receipt-123",
                correlation_id="corr-123",
                error=None,
            )

    workbench = FakeWorkbench()
    service.workbench = workbench

    def generate_direct(*, messages, model, role, tools=()):
        provider.calls.append((role, tuple(tool.name for tool in tools)))
        if role == "director_conversation":
            return DirectorModelResponse(
                content="",
                model=model or "nvidia/nemotron-3-ultra-550b-a55b",
                provider="gemini",
                request_id="request-wb-1",
                execution_id="execution-wb-1",
                tool_calls=(
                    DirectorToolCall(
                        call_id="call-wb-1",
                        name="workbench",
                        arguments={
                            "capability": "file:read",
                            "arguments": ["notes.txt"],
                        },
                    ),
                ),
            )
        assert role == "director_conversation_result"
        last = messages[-1]["content"]
        assert isinstance(last, list)
        assert "authoritative file evidence" in last[0]["text"]
        return DirectorModelResponse(
            content="He comprobado la evidencia directamente con el Workbench.",
            model=model or "nvidia/nemotron-3-ultra-550b-a55b",
            provider="gemini",
            request_id="request-wb-2",
            execution_id="execution-wb-2",
        )

    provider.generate = generate_direct
    session = service.create_session()
    response = service.send_message(
        session_id=session.session_id,
        user_input="Comprueba directamente las notas del proyecto.",
    )

    assert response.response_text.startswith("He comprobado")
    assert len(workbench.requests) == 1
    assert workbench.requests[0].capability == "workbench:file:read"
    assert workbench.requests[0].actor == "director"
    assert workbench.requests[0].mission_id.startswith("conversation:")
    assert ("director_conversation", ("workbench",)) in provider.calls
    assert ("director_conversation_result", ()) in provider.calls


def test_mission_evidence_includes_durable_identity_and_planning_metadata():
    task = SimpleNamespace(
        task=SimpleNamespace(
            task_id="task-readonly-1",
            role="operator",
            capability="workbench:census",
        ),
        status=SimpleNamespace(value="succeeded"),
        error=None,
    )
    plan = SimpleNamespace(
        objective="Verify governed runtime",
        model="nvidia/test-model",
        provider="nvidia_api",
        execution_id="planning-exec-123",
    )
    mission = SimpleNamespace(
        submission=SimpleNamespace(mission_id="mission-durable-123", plan=plan),
        status=SimpleNamespace(value="succeeded"),
        tasks=(task,),
    )

    evidence = DirectorConversationService._mission_evidence(mission)

    assert evidence["mission_id"] == "mission-durable-123"
    assert evidence["status"] == "succeeded"
    assert evidence["objective"] == "Verify governed runtime"
    assert evidence["model"] == "nvidia/test-model"
    assert evidence["provider"] == "nvidia_api"
    assert evidence["planning_execution_id"] == "planning-exec-123"
    assert evidence["task_count"] == 1
    assert evidence["tasks"][0]["task_id"] == "task-readonly-1"


def test_mission_evidence_restores_persisted_result_after_runtime_restart():
    task_result = SimpleNamespace(
        task=SimpleNamespace(
            task_id="task-research-1",
            role="researcher",
            capability="workbench:research:retrieve",
            depends_on=(),
        ),
        status=SimpleNamespace(value="succeeded"),
        error=None,
        capability_result=None,
        model_result=None,
        durable_result={
            "type": "workbench",
            "status": "succeeded",
            "operation": "research_retrieve",
            "output": {"sources": [{"source_id": "src-1", "url": "https://example.test"}]},
            "artifact_id": "artifact-1",
            "provenance": "aurora-research:retrieval",
            "receipt_id": "receipt-1",
            "correlation_id": "corr-1",
        },
    )
    mission = SimpleNamespace(
        submission=SimpleNamespace(
            mission_id="mission-restored-results",
            plan=SimpleNamespace(
                objective="retrieve research sources",
                model="nvidia/test-model",
                provider="nvidia_api",
                execution_id="planning-exec",
            ),
        ),
        status=SimpleNamespace(value="succeeded"),
        tasks=(task_result,),
        events=(),
    )

    evidence = DirectorConversationService._mission_evidence(mission)

    assert evidence["status"] == "succeeded"
    assert evidence["tasks"][0]["result"] == {
        "sources": [{"source_id": "src-1", "url": "https://example.test"}]
    }
    assert evidence["tasks"][0]["artifact_id"] == "artifact-1"
    assert evidence["tasks"][0]["receipt_id"] == "receipt-1"
    assert evidence["tasks"][0]["provenance"] == "aurora-research:retrieval"
    assert evidence["tasks"][0]["correlation_id"] == "corr-1"


def test_conversation_executes_native_explicit_readonly_tasks_and_returns_durable_evidence(tmp_path: Path):
    service, _ = base_service(tmp_path)

    class ExplicitTaskProvider:
        provider_id = "nvidia_api"

        def generate(self, *, messages, model, role, tools=()):
            if role == "director_conversation":
                return DirectorModelResponse(
                    content="",
                    model="nvidia/test-model",
                    provider="nvidia_api",
                    request_id="request-explicit-1",
                    execution_id="conversation-planning-exec-123",
                    tool_calls=(
                        DirectorToolCall(
                            call_id="call-explicit-1",
                            name="run_mission",
                            arguments={
                                "name": "Validación Durable de Aurora",
                                "tasks": [
                                    {"id": "task-census", "type": "workbench",
                                     "operation": "census", "arguments": {}},
                                    {"id": "task-preflight", "type": "workbench",
                                     "operation": "preflight", "dependencies": ["task-census"]},
                                    {"id": "task-file-list", "type": "workbench",
                                     "operation": "file:list", "arguments": ["."],
                                     "dependencies": ["task-preflight"]},
                                ],
                            },
                        ),
                    ),
                )
            assert role == "director_conversation_result"
            return DirectorModelResponse(
                content="La misión ha terminado. Adjunto la evidencia real recibida del runtime.",
                model="nvidia/test-model",
                provider="nvidia_api",
                request_id="request-explicit-2",
                execution_id="conversation-summary-exec-456",
            )

    provider = ExplicitTaskProvider()
    service.set_provider(provider)
    captured = {}

    def runner(*, objective, tasks, planning_model, planning_provider, planning_execution_id):
        captured.update({
            "objective": objective,
            "tasks": tasks,
            "planning_model": planning_model,
            "planning_provider": planning_provider,
            "planning_execution_id": planning_execution_id,
        })
        plan = SimpleNamespace(
            objective=objective,
            model=planning_model,
            provider=planning_provider,
            execution_id=planning_execution_id,
        )
        task_results = []
        for task in tasks:
            output = {"capability": task.capability, "read_only": True}
            task_results.append(SimpleNamespace(
                task=task,
                status=SimpleNamespace(value="succeeded"),
                error=None,
                capability_result=SimpleNamespace(execution=SimpleNamespace(
                    output=output,
                    artifact_id=None,
                    receipt_id=f"receipt-{task.task_id}",
                    provenance=f"test:{task.capability}",
                    correlation_id=f"corr-{task.task_id}",
                )),
            ))
        return SimpleNamespace(
            submission=SimpleNamespace(mission_id="mission-explicit-123", plan=plan),
            status=SimpleNamespace(value="succeeded"),
            tasks=tuple(task_results),
            events=(
                SimpleNamespace(event_id="event-1", event_type="mission_submitted", task_id=None),
                SimpleNamespace(event_id="event-2", event_type="mission_finalized", task_id=None),
            ),
        )

    service.mission_runner = runner
    session = service.create_session()
    response = service.send_message(
        session_id=session.session_id,
        user_input="Ejecuta la misión gobernada de validación de solo lectura.",
    )

    assert captured["objective"] == "Validación Durable de Aurora"
    assert captured["planning_model"] == "nvidia/test-model"
    assert captured["planning_provider"] == "nvidia_api"
    assert captured["planning_execution_id"] == "conversation-planning-exec-123"
    tasks = captured["tasks"]
    assert [task.task_id for task in tasks] == ["task-census", "task-preflight", "task-file-list"]
    assert [task.capability for task in tasks] == [
        "workbench:census", "workbench:preflight", "workbench:file:list"
    ]
    assert tasks[1].depends_on == ("task-census",)
    assert tasks[2].depends_on == ("task-preflight",)
    assert tasks[2].args == (".",)
    assert '"mission_id": "mission-explicit-123"' in response.response_text
    assert '"planning_execution_id": "conversation-planning-exec-123"' in response.response_text
    assert '"event_type": "mission_submitted"' in response.response_text
    assert '"read_only": true' in response.response_text
    assert "EVIDENCIA_AUTORITATIVA_DE_EJECUCION (JSON)" in response.response_text


def test_explicit_mission_rejects_workbench_writes_before_runner_invocation(tmp_path: Path):
    try:
        DirectorConversationService._normalize_mission_tool_arguments({
            "name": "Unsafe explicit task request",
            "tasks": [{
                "id": "write-task",
                "type": "workbench",
                "operation": "file:write",
                "arguments": ["notes.txt", "changed"],
            }],
        })
    except Exception as exc:
        assert "read-only Workbench operations" in str(exc)
    else:
        raise AssertionError("explicit task graph must reject write capabilities")


def test_mission_evidence_preserves_failed_or_blocked_durable_terminal_state():
    task = SimpleNamespace(
        task=SimpleNamespace(
            task_id="task-1",
            role="operator",
            capability="workbench:census",
            depends_on=(),
        ),
        status=SimpleNamespace(value="succeeded"),
        error=None,
        capability_result=None,
        model_result=None,
    )
    plan = SimpleNamespace(
        objective="Validate durable terminal state",
        model="nvidia/test-model",
        provider="nvidia_api",
        execution_id="planning-exec",
    )
    for terminal_state in ("failed", "blocked"):
        mission = SimpleNamespace(
            submission=SimpleNamespace(mission_id=f"mission-{terminal_state}", plan=plan),
            status=SimpleNamespace(value=terminal_state),
            tasks=(task,),
            events=(),
        )
        evidence = DirectorConversationService._mission_evidence(mission)
        assert evidence["status"] == terminal_state



def test_explicit_mission_request_cannot_be_replaced_by_direct_workbench_call(tmp_path: Path):
    service, _ = base_service(tmp_path)
    provider = ToolProvider()
    service.set_provider(provider)
    user_input = (
        "Quiero que planifiques y ejecutes una misión multiagente nueva "
        "para revisar la arquitectura de Aurora."
    )

    class FakeWorkbench:
        def __init__(self):
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            raise AssertionError("direct Workbench must not replace a mission request")

    workbench = FakeWorkbench()
    service.workbench = workbench
    executed = []

    def runner(*, objective):
        executed.append(objective)
        plan = SimpleNamespace(
            objective=objective,
            model="nvidia/test-model",
            provider="nvidia_api",
            execution_id="mission-planning-exec",
        )
        return SimpleNamespace(
            submission=SimpleNamespace(
                mission_id="mission-routing-456",
                plan=plan,
            ),
            status=SimpleNamespace(value="succeeded"),
            tasks=(),
            events=(),
        )

    service.mission_runner = runner

    def misrouted_provider(*, messages, model, role, tools=()):
        provider.calls.append((role, tuple(tool.name for tool in tools)))
        if role == "director_conversation":
            return DirectorModelResponse(
                content="",
                model=model or "nvidia/test-model",
                provider="nvidia_api",
                request_id="request-misrouted-1",
                execution_id="execution-misrouted-1",
                tool_calls=(
                    DirectorToolCall(
                        call_id="call-misrouted-1",
                        name="workbench",
                        arguments={
                            "capability": "file:read",
                            "arguments": ["README.md"],
                        },
                    ),
                ),
            )
        assert role == "director_conversation_result"
        final_content = messages[-1]["content"]
        assert isinstance(final_content, list)
        assert "mission-routing-456" in final_content[0]["text"]
        return DirectorModelResponse(
            content="La misión fue enviada y hay evidencia del runtime.",
            model=model or "nvidia/test-model",
            provider="nvidia_api",
            request_id="request-misrouted-2",
            execution_id="execution-misrouted-2",
        )

    provider.generate = misrouted_provider
    session = service.create_session()
    response = service.send_message(
        session_id=session.session_id,
        user_input=user_input,
    )

    assert executed == [user_input]
    assert workbench.requests == []
    assert ("director_conversation", ("run_mission",)) in provider.calls
    assert "mission-routing-456" in response.response_text
    assert "explicit_mission_request_without_run_mission_tool_call" in response.response_text
