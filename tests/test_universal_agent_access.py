from pathlib import Path

from aurora.autonomy.internet import InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.inference.models import InferenceRequest, InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import OrchestrationEngine
from aurora.workbench import UniversalWorkbench


class Gateway:
    def __init__(self):
        self.policy = InternetPolicy(
            frozenset({"https"}),
            frozenset({"api.github.com", "example.com"}),
        )


class ToolLoopRouter(ModelRouter):
    def __init__(self):
        super().__init__([
            ModelSpec(
                "fake/model",
                "fake",
                "https://example.invalid",
                capabilities=("reasoning",),
            )
        ])
        self.calls = 0

    def generate(self, request, capability=None, preferred_models=None):
        self.calls += 1
        content = (
            '{"workbench_calls":[{"capability":"workbench:file:read","arguments":["note.txt"]}]}'
            if self.calls == 1
            else "The shared Workbench returned the file evidence successfully."
        )
        return InferenceResponse(
            model="fake/model",
            provider="fake",
            content=content,
            request_id=request.request_id,
            execution_id=f"execution-{self.calls}",
        )


def test_reasoning_agent_has_runtime_access_to_real_workbench(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "note.txt").write_text("hello from Aurora", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    workbench = UniversalWorkbench(
        repository_root=repo,
        runtime_root=runtime,
        gateway=Gateway(),
        policy=Policy(frozenset({"workbench:*"}), frozenset()),
    )
    router = ToolLoopRouter()
    engine = OrchestrationEngine(router, workbench=workbench)

    result = engine.run_task(
        task_id="task-universal-access",
        role="researcher",
        objective="Read note.txt and report the authoritative result.",
    )

    assert result.response.content.startswith("The shared Workbench")
    assert router.calls == 2
