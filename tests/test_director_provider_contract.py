from aurora.inference.router import ModelRouter
import pytest

from aurora.orchestration.director import DirectorPlanError, DirectorPlanner
from aurora.orchestration.director_provider import (
    DirectorModelResponse,
    DirectorToolCall,
    InferenceDirectorProvider,
)


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


class ToolCallDirectorProvider(FakeDirectorProvider):
    def generate(self, *, messages, model, role, tools=()):
        assert [tool.name for tool in tools] == ["delegate_task"]
        return DirectorModelResponse(
            content="",
            model=model or "fake/director",
            provider=self.provider_id,
            request_id="request-tool",
            execution_id="execution-tool",
            tool_calls=(
                DirectorToolCall(
                    call_id="call-1",
                    name="delegate_task",
                    arguments={
                        "task_id": "research",
                        "role": "researcher",
                        "objective": "research evidence",
                        "capability": None,
                        "args": [],
                        "depends_on": [],
                        "skills": [],
                    },
                ),
            ),
        )


def test_director_tool_call_is_authorized_into_a_task_without_execution():
    planner = DirectorPlanner(router=ModelRouter([]), provider=ToolCallDirectorProvider())
    plan = planner.plan(objective="research Aurora")
    assert plan.tasks[0].objective == "research evidence"


def test_unknown_director_tool_is_rejected_before_execution():
    class UnknownToolProvider(ToolCallDirectorProvider):
        def generate(self, *, messages, model, role, tools=()):
            response = super().generate(messages=messages, model=model, role=role, tools=tools)
            return DirectorModelResponse(
                content=response.content,
                model=response.model,
                provider=response.provider,
                request_id=response.request_id,
                execution_id=response.execution_id,
                tool_calls=(
                    DirectorToolCall("call-unknown", "run_shell", {}),
                ),
            )

    planner = DirectorPlanner(router=ModelRouter([]), provider=UnknownToolProvider())
    with pytest.raises(DirectorPlanError, match="unregistered tool"):
        planner.plan(objective="unsafe")


def test_malformed_director_tool_arguments_are_rejected():
    class MalformedProvider(ToolCallDirectorProvider):
        def generate(self, *, messages, model, role, tools=()):
            return DirectorModelResponse(
                content="",
                model="fake/director",
                provider=self.provider_id,
                request_id="request-malformed",
                execution_id="execution-malformed",
                tool_calls=(DirectorToolCall("call-bad", "delegate_task", {"task_id": "x"}),),
            )

    planner = DirectorPlanner(router=ModelRouter([]), provider=MalformedProvider())
    with pytest.raises(DirectorPlanError, match="missing required argument"):
        planner.plan(objective="malformed")
