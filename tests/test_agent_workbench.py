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


def test_agent_response_retains_failed_workbench_receipt_even_if_final_text_claims_success():
    class FailedWorkbench(FakeWorkbench):
        def execute(self, request):
            self.calls.append(request)
            return WorkbenchResult(
                capability=request.capability,
                operation="file_write",
                status="failed",
                output=None,
                artifact_id=None,
                provenance="fake-workbench",
                error="controlled write failure",
                receipt_id="receipt-failed",
                correlation_id="corr-failed",
            )

    from aurora.inference.models import InferenceRequest

    workbench = FailedWorkbench()
    router = FakeRouter()
    router.responses = [
        '{"workbench_calls":[{"capability":"workbench:file:write","arguments":["out.txt","done"]}]}',
        "The file was written successfully.",
    ]
    response = AgentWorkbenchToolLoop(workbench).run(
        request=InferenceRequest(
            messages=[{"role": "user", "content": "Write the result file."}],
            metadata={"role": "engineer", "task_id": "task-write"},
        ),
        actor="engineer",
        mission_id="mission-write",
        reason="Write the result file",
        task_id="task-write",
        router=router,
    )

    assert response.content == "The file was written successfully."
    assert len(response.workbench_receipts) == 1
    receipt = response.workbench_receipts[0]
    assert receipt["status"] == "failed"
    assert receipt["error"] == "controlled write failure"
    assert receipt["receipt_id"] == "receipt-failed"


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



def test_agent_workbench_uses_runtime_owned_stable_key_and_work_id_across_retries():
    workbench = FakeWorkbench()
    workbench.session_store = object()
    router = FakeRouter()
    loop = AgentWorkbenchToolLoop(workbench)
    from aurora.inference.models import InferenceRequest

    request = InferenceRequest(
        messages=[{"role": "user", "content": "Write one controlled task output."}],
        metadata={
            "role": "engineer",
            "task_id": "write-task",
            "project_id": "project-a",
            "correlation_id": "corr-authoritative",
        },
    )
    for supplied_key, supplied_work_id in (
        ("model-key-one", "model-work-one"),
        ("model-key-two", "model-work-two"),
    ):
        router.responses = [
            '{"workbench_calls":[{"capability":"workbench:file:write","arguments":["out.txt","done"],"options":{"work_id":"'
            + supplied_work_id
            + '","idempotency_key":"'
            + supplied_key
            + '","project_id":"project-a","correlation_id":"model-chosen"}}]}',
            "The write request was processed.",
        ]
        loop.run(
            request=request,
            actor="engineer",
            mission_id="mission-1",
            reason="write a controlled task output",
            task_id="write-task",
            router=router,
        )

    first, second = workbench.calls
    assert first.idempotency_key is not None
    assert first.idempotency_key == second.idempotency_key
    assert first.idempotency_key.startswith("agent-task:mission-1:write-task:call:")
    assert first.options["work_id"] == second.options["work_id"] == "mission-1"
    assert first.options["project_id"] == second.options["project_id"] == "project-a"
    assert "correlation_id" not in first.options
    assert first.correlation_id == second.correlation_id == "corr-authoritative"
    assert "idempotency_key" not in first.options
    assert first.idempotency_key not in {"model-key-one", "model-key-two"}



def test_agent_workbench_rejects_conflicting_project_before_execution():
    workbench = FakeWorkbench()
    workbench.session_store = object()
    router = FakeRouter()
    router.responses = [
        '{"workbench_calls":[{"capability":"workbench:file:write","arguments":["out.txt","wrong project"],"options":{"project_id":"project-b"}}]}',
        "The project conflict was rejected.",
    ]
    from aurora.inference.models import InferenceRequest

    request = InferenceRequest(
        messages=[{"role": "user", "content": "Write a file in the selected project."}],
        metadata={
            "role": "engineer",
            "task_id": "task-project-scope",
            "project_id": "project-a",
            "correlation_id": "corr-project-a",
        },
    )
    response = AgentWorkbenchToolLoop(workbench).run(
        request=request,
        actor="engineer",
        mission_id="mission-project-a",
        reason="write a project-scoped file",
        task_id="task-project-scope",
        router=router,
    )

    assert workbench.calls == []
    assert len(response.workbench_receipts) == 1
    receipt = response.workbench_receipts[0]
    assert receipt["status"] == "failed"
    assert receipt["operation"] == "project_scope_validation"
    assert receipt["requested_project_id"] == "project-b"
    assert receipt["project_id"] == "project-a"
    assert "conflicts with the authoritative task project" in receipt["error"]
    assert "WORKBENCH_RESULTS" in str(router.requests[1].messages[-1]["content"])
