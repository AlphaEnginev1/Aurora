import os

import pytest

from aurora.orchestration.director import DirectorPlanner, DirectorService
from aurora.orchestration.director_provider import DirectorModelResponse, DirectorToolSpec
from aurora.orchestration.gemini_provider import GeminiDirectorProvider


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


class RecordingTaskEngine:
    def __init__(self):
        self.received = None

    def execute(self, tasks):
        self.received = tuple(tasks)
        return self.received


@pytest.mark.live
def test_live_gemini_director_integrates_with_director_service(live_gemini_response):
    task_engine = RecordingTaskEngine()
    planner = DirectorPlanner(
        provider=ReplayDirectorProvider(live_gemini_response),
        router=None,
    )

    service = DirectorService(planner, task_engine)
    result = service.run_mission(
        objective="Create exactly one research task to investigate Aurora architecture.",
    )

    assert result.plan.model
    assert result.plan.tasks
    assert len(result.plan.tasks) == 1
    task = result.plan.tasks[0]
    assert task.task_id
    assert task.role
    assert task.objective
    assert task_engine.received == result.plan.tasks
