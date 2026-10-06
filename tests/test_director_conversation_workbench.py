from types import SimpleNamespace
from pathlib import Path

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
                            "capability": "workbench:file:read",
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
