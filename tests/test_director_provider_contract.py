from aurora.inference.router import ModelRouter
from aurora.orchestration.director import DirectorPlanner
from aurora.orchestration.director_provider import DirectorModelResponse, InferenceDirectorProvider


class FakeDirectorProvider:
    provider_id = "fake_director"

    def generate(self, *, messages, model, role):
        assert role in {"director_planner", "director_reviewer"}
        return DirectorModelResponse(
            content='{"tasks":[{"task_id":"research","role":"researcher","objective":"research evidence","capability":null,"args":[],"depends_on":[],"skills":[]}]}'
            if role == "director_planner"
            else '{"decision":"complete","rationale":"evidence is sufficient","tasks":[]}',
            model=model or "fake/director",
            provider=self.provider_id,
            request_id="request-1",
            execution_id="execution-1",
        )


def test_director_planner_accepts_non_router_provider():
    planner = DirectorPlanner(router=ModelRouter([]), provider=FakeDirectorProvider())
    plan = planner.plan(objective="research Aurora")
    assert plan.model == "fake/director"
    assert plan.tasks[0].task_id == "research"


def test_inference_adapter_preserves_existing_router_path():
    adapter = InferenceDirectorProvider(ModelRouter([]))
    assert adapter.provider_id == "inference_router"
