from types import SimpleNamespace
from pathlib import Path

from aurora.orchestration.director_conversation import DirectorConversationService
from aurora.orchestration.director_provider import DirectorModelResponse, DirectorToolCall
from tests.test_director_conversation import _service as base_service


class ToolProvider:
    provider_id = "gemini"

    def __init__(self):
        self.calls = []

    def generate(self, *, messages, model, role, tools=()):
        self.calls.append((role, tuple(tool.name for tool in tools)))
        if role == "director_conversation":
            return DirectorModelResponse(
                content="",
                model=model or "gemini-3.7-flash",
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
            model=model or "gemini-3.7-flash",
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
