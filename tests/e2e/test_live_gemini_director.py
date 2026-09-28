import os

import pytest

from aurora.orchestration.director import DirectorPlanner
from aurora.orchestration.director_provider import DirectorToolSpec
from aurora.orchestration.gemini_provider import GeminiDirectorProvider


@pytest.mark.live
def test_live_gemini_director_tool_call():
    if not os.getenv("NVIDIA_GEMINI_LIVE"):
        pytest.skip("live Gemini gate is disabled")
    provider = GeminiDirectorProvider()
    response = provider.generate(
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
    assert response.provider == "gemini"
    assert response.tool_calls
    assert response.tool_calls[0].name == "delegate_task"
    assert response.tool_calls[0].arguments["task_id"]


class RecordingTaskEngine:
    def __init__(self):
        self.received = None

    def execute(self, tasks):
        self.received = tuple(tasks)
        return self.received


@pytest.mark.live
def test_live_gemini_director_integrates_with_director_service():
    if not os.getenv("NVIDIA_GEMINI_LIVE"):
        pytest.skip("live Gemini gate is disabled")

    task_engine = RecordingTaskEngine()
    planner = DirectorPlanner(provider=GeminiDirectorProvider(), router=None)
    from aurora.orchestration.director import DirectorService

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
