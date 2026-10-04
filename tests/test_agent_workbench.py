from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.agent_workbench import (
    AgentWorkbenchToolError,
    AgentWorkbenchToolLoop,
    parse_workbench_calls,
)
from aurora.workbench.models import WorkbenchResult


class FakeWorkbench:
    def __init__(self):
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        output = (
            [{"mime_type": "image/png", "data": "ZmFrZS1zY3JlZW5zaG90"}]
            if request.capability == "workbench:computer"
            else {"value": request.arguments[0] if request.arguments else "ok"}
        )
        return WorkbenchResult(
            capability=request.capability,
            operation=request.capability.split(":", 1)[-1],
            status="succeeded",
            output=output,
            artifact_id="artifact-1",
            provenance="fake-workbench",
        )


class FakeRouter(ModelRouter):
    def __init__(self):
        super().__init__([ModelSpec(
            "fake/model",
            "fake",
            "https://example.invalid",
            capabilities=("reasoning",),
        )])
        self.responses = [
            '{"workbench_calls":[{"capability":"workbench:file:read","arguments":["README.md"]}]}',
            "Evidence verified: the requested file was read.",
        ]
        self.requests = []

    def generate(self, request, capability=None, preferred_models=None):
        self.requests.append(request)
        return InferenceResponse(
            model="fake/model",
            provider="fake",
            content=self.responses.pop(0),
            request_id=request.request_id,
            execution_id=f"exec-{len(self.requests)}",
        )


def test_parse_workbench_calls_is_strict_and_provider_neutral():
    calls = parse_workbench_calls(
        '{"workbench_calls":[{"capability":"workbench:file:read","arguments":["README.md"]}]}'
    )
    assert len(calls) == 1
    assert calls[0].capability == "workbench:file:read"
    assert calls[0].arguments == ("README.md",)


def test_agent_reasoning_can_request_and_receive_workbench_evidence():
    workbench = FakeWorkbench()
    router = FakeRouter()
    loop = AgentWorkbenchToolLoop(workbench)

    from aurora.inference.models import InferenceRequest

    request = InferenceRequest(
        messages=[{"role": "user", "content": "Read the project README and report what you find."}],
        metadata={"role": "engineer", "task_id": "task-1"},
    )
    response = loop.run(
        request=request,
        actor="engineer",
        mission_id="task-1",
        reason="Read the project README",
        router=router,
    )

    assert response.content.startswith("Evidence verified")
    assert len(workbench.calls) == 1
    assert workbench.calls[0].capability == "workbench:file:read"
    assert workbench.calls[0].arguments == ("README.md",)
    assert any("WORKBENCH_RESULTS" in str(message["content"]) for message in router.requests[1].messages)


def test_agent_workbench_budget_fails_closed():
    workbench = FakeWorkbench()
    router = FakeRouter()
    loop = AgentWorkbenchToolLoop(workbench, max_calls=1)

    from aurora.inference.models import InferenceRequest

    request = InferenceRequest(
        messages=[{"role": "user", "content": "Use the Workbench."}],
        metadata={"role": "engineer", "task_id": "task-1"},
    )
    router.responses = [
        '{"workbench_calls":[{"capability":"workbench:file:read","arguments":["README.md"]},{"capability":"workbench:file:read","arguments":["ROADMAP.md"]}]}'
    ]
    try:
        loop.run(
            request=request,
            actor="engineer",
            mission_id="task-1",
            reason="Use the Workbench",
            router=router,
        )
    except AgentWorkbenchToolError as exc:
        assert "budget" in str(exc)
    else:
        raise AssertionError("tool loop must fail closed when its call budget is exceeded")


def test_agent_receives_native_media_evidence_from_workbench():
    workbench = FakeWorkbench()
    router = FakeRouter()
    router.responses = [
        '{"workbench_calls":[{"capability":"workbench:computer","arguments":["[{\\"action\\":\\"screenshot\\"}]"]}]}',
        "I inspected the screenshot evidence.",
    ]
    loop = AgentWorkbenchToolLoop(workbench)

    from aurora.inference.models import InferenceRequest

    response = loop.run(
        request=InferenceRequest(
            messages=[{"role": "user", "content": "Take a screenshot and inspect it."}],
        ),
        actor="researcher",
        mission_id="mission-media",
        reason="Inspect screenshot",
        router=router,
    )

    assert response.content.startswith("I inspected")
    evidence_message = router.requests[1].messages[-1]
    assert isinstance(evidence_message["content"], list)
    assert evidence_message["content"][1]["modality"] == "image"
    assert evidence_message["content"][1]["mime_type"] == "image/png"
