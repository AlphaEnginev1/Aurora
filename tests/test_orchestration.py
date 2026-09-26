from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import OrchestrationEngine

class FakeRouter(ModelRouter):
    def generate(self, request, capability=None):
        assert capability == "reasoning"
        assert request.metadata["task_id"] == "task-1"
        assert request.metadata["role"] == "researcher"
        return InferenceResponse(model="fake/model", provider="fake", content="verified")

def test_orchestration_routes_agent_task():
    engine = OrchestrationEngine(FakeRouter([ModelSpec("fake/model", "fake", "https://example.invalid")]))
    result = engine.run_task(task_id="task-1", role="researcher", objective="Investigate the target.")
    assert result.task_id == "task-1"
    assert result.role == "researcher"
    assert result.response.content == "verified"
