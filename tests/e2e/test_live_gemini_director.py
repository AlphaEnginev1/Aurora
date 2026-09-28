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
