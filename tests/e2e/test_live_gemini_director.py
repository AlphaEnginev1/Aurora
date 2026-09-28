import os

import pytest

from aurora.orchestration.director import DirectorPlanError, DirectorPlanner
from aurora.orchestration.gemini_provider import GeminiDirectorProvider


@pytest.mark.live
def test_live_gemini_director_tool_call():
    if not os.getenv("NVIDIA_GEMINI_LIVE"):
        pytest.skip("live Gemini gate is disabled")
    provider = GeminiDirectorProvider()
    planner = DirectorPlanner(router=None, provider=provider)
    plan = planner.plan(
        objective="Create exactly one research task to investigate Aurora architecture. Use the delegate_task tool.",
    )
    assert len(plan.tasks) == 1
    assert plan.tasks[0].role
    assert plan.tasks[0].objective
