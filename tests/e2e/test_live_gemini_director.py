import os

import pytest

from aurora.inference.models import InferenceRequest, InferenceResponse
from aurora.orchestration.director import DirectorPlanner, DirectorService
from aurora.orchestration.director_provider import DirectorModelResponse, DirectorToolSpec
from aurora.orchestration.engine import OrchestrationEngine
from aurora.orchestration.gemini_provider import GeminiDirectorProvider
from aurora.orchestration.task_engine import TaskEngine, TaskStatus


@pytest.fixture(scope="module")
def live_gemini_response() -> DirectorModelResponse:
    if not os.getenv("NVIDIA_GEMINI_LIVE"):
        pytest.skip("live Gemini gate is disabled")

    provider = GeminiDirectorProvider()
    return provider.generate(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Aurora's Director. You may only propose work through the "
                    "delegate_task tool. Do not answer with prose."
                ),
            },
            {
                "role": "user",
                "content": "Create exactly one research task to investigate Aurora architecture.",
            },
        ],
        model=None,
        role="director_planner",
        tools=(
            DirectorToolSpec(
                name="delegate_task",
                description="Request Aurora to validate and enqueue one delegated task.",
                parameters={
                    "type": "object",
                    "required": ["task_id", "role", "objective"],
                    "properties": {
                        "task_id": {"type": "string"},
                        "role": {"type": "string"},
                        "objective": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
        ),
    )


@pytest.mark.live
def test_live_gemini_director_tool_call(live_gemini_response):
    response = live_gemini_response

    assert response.provider == "gemini"
    assert response.tool_calls
    assert response.tool_calls[0].name == "delegate_task"
    assert response.tool_calls[0].arguments["task_id"]


class ReplayDirectorProvider:
    """Replay a real Gemini response without issuing a second external API call."""

    provider_id = "gemini"

    def __init__(self, response: DirectorModelResponse):
        self.response = response

    def generate(self, messages, model, role, tools=()):
        return self.response


class ControlledRouter:
    """Deterministic model boundary for real TaskEngine execution."""

    def __init__(self):
        self.requests: list[InferenceRequest] = []

    def generate(self, request, *, capability="reasoning", preferred_models=None):
        self.requests.append(request)
        return InferenceResponse(
            model="controlled-task-model",
            provider="controlled",
            content="controlled execution result",
            request_id=request.request_id,
            execution_id="controlled-execution",
        )


@pytest.mark.live
def test_live_gemini_director_executes_through_real_task_engine(
    live_gemini_response,
    tmp_path,
):
    router = ControlledRouter()
    orchestration = OrchestrationEngine(router)
    task_engine = TaskEngine(
        orchestration=orchestration,
        workspace_root=tmp_path,
    )
    planner = DirectorPlanner(
        provider=ReplayDirectorProvider(live_gemini_response),
        router=None,
    )
    service = DirectorService(planner, task_engine)

    result = service.run_mission(
        objective="Create exactly one research task to investigate Aurora architecture.",
    )

    assert result.plan.model
    assert len(result.plan.tasks) == 1
    task = result.plan.tasks[0]

    assert len(result.tasks) == 1
    execution = result.tasks[0]
    assert execution.task == task
    assert execution.status is TaskStatus.SUCCEEDED
    assert execution.model_result is not None
    assert execution.model_result.response.content == "controlled execution result"

    assert len(router.requests) == 1
    request = router.requests[0]
    assert request.metadata["task_id"] == task.task_id
    assert request.metadata["role"] == task.role
