from aurora.inference.models import InferenceResponse
from aurora.orchestration.director_provider import DirectorToolSpec, InferenceDirectorProvider
from aurora.orchestration.director_provider_failover import BoundInferenceDirectorProvider


class FakeRouter:
    def generate(self, request, *, capability):
        return InferenceResponse(
            model="fake/director",
            provider="fake",
            content='{"tool_calls":[{"name":"run_mission","arguments":{"objective":"inspect repository"}}]}',
            request_id=request.request_id,
            execution_id="exec-1",
        )


def test_inference_director_provider_emits_contract_tool_calls():
    provider = InferenceDirectorProvider(FakeRouter())
    result = provider.generate(
        messages=[{"role":"system","content":"You are Aurora."},{"role":"user","content":"Inspect it."}],
        model=None,
        role="director_conversation",
        tools=(
            DirectorToolSpec(
                name="run_mission",
                description="Run an Aurora mission.",
                parameters={"type":"object"},
            ),
        ),
    )
    assert result.content == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "run_mission"
    assert result.tool_calls[0].arguments["objective"] == "inspect repository"

def test_inference_director_provider_parses_nvidia_structured_text_tool_call():
    provider = InferenceDirectorProvider(FakeRouter())
    content = (
        '[run_mission source=researcher scope=director provenance=mission-creator]\n'
        'task: "Responde exactamente: AURORA_E2E_TEST_OK"\n'
        'department: research\n'
        'priority: normal\n'
        '[/run_mission]'
    )
    result = provider._parse_tool_calls(
        content,
        (
            DirectorToolSpec(
                name="run_mission",
                description="Run an Aurora mission.",
                parameters={"type": "object"},
            ),
        ),
    )
    assert len(result) == 1
    assert result[0].name == "run_mission"
    assert result[0].arguments == {"objective": "Responde exactamente: AURORA_E2E_TEST_OK"}


def test_inference_director_provider_rejects_unknown_structured_text_tool():
    provider = InferenceDirectorProvider(FakeRouter())
    content = '[run_shell source=director]\ntask: "danger"\n[/run_shell]'
    try:
        provider._parse_tool_calls(
            content,
            (
                DirectorToolSpec(
                    name="run_mission",
                    description="Run an Aurora mission.",
                    parameters={"type": "object"},
                ),
            ),
        )
    except RuntimeError as exc:
        assert "unregistered tool" in str(exc)
    else:
        raise AssertionError("unknown structured tool call must fail closed")


class CapturingToolRouter:
    def __init__(self, content):
        self.content = content
        self.request = None

    def generate(self, request, *, capability):
        self.request = request
        return InferenceResponse(
            model=request.model or "fake/director",
            provider="fake",
            content=self.content,
            request_id=request.request_id,
            execution_id="exec-bound-1",
        )


def test_bound_inference_director_provider_exposes_and_parses_governed_tools():
    router = CapturingToolRouter(
        '{"tool_calls":[{"name":"run_mission","arguments":{"objective":"inspect repository"}}]}'
    )
    provider = BoundInferenceDirectorProvider(
        router,
        provider_id="nvidia_api",
        model="nvidia/free-test-model",
    )
    result = provider.generate(
        messages=[
            {"role": "system", "content": "You are Aurora Director."},
            {"role": "user", "content": "Inspect the project."},
        ],
        model=None,
        role="director_conversation",
        tools=(
            DirectorToolSpec(
                name="run_mission",
                description="Execute a governed Aurora mission.",
                parameters={
                    "type": "object",
                    "required": ["objective"],
                    "properties": {"objective": {"type": "string"}},
                },
            ),
        ),
    )

    assert result.content == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "run_mission"
    assert result.tool_calls[0].arguments == {"objective": "inspect repository"}
    assert router.request is not None
    assert router.request.metadata["director_tools"] == ["run_mission"]
    assert "Tool mode is enabled" in router.request.messages[0]["content"]


def test_inference_director_provider_parses_native_nvidia_mission_task_graph():
    content = (
        "✦\\n<|tool_call_start|>[run_mission(name='Validación Durable de Aurora', tasks=["
        "{'id': 'task-census', 'type': 'workbench', 'operation': 'census', 'arguments': {}}, "
        "{'id': 'task-preflight', 'type': 'workbench', 'operation': 'preflight', "
        "'dependencies': ['task-census']}, "
        "{'id': 'task-file-list', 'type': 'workbench', 'operation': 'file:list', "
        "'arguments': ['.'], 'dependencies': ['task-preflight']}])]<|tool_call_end|>"
    )
    tools = (
        DirectorToolSpec(
            name="run_mission",
            description="Execute a governed Aurora mission.",
            parameters={"type": "object"},
        ),
    )

    calls = InferenceDirectorProvider._parse_tool_calls(content, tools)

    assert len(calls) == 1
    assert calls[0].name == "run_mission"
    assert calls[0].arguments["name"] == "Validación Durable de Aurora"
    assert [item["id"] for item in calls[0].arguments["tasks"]] == [
        "task-census", "task-preflight", "task-file-list"
    ]
    assert calls[0].arguments["tasks"][2]["arguments"] == ["."]


def test_inference_director_provider_never_evaluates_native_tool_call_code(tmp_path):
    marker = tmp_path / "native-tool-call-executed"
    content = (
        f"✦\\n<|tool_call_start|>[run_mission("
        f"objective=__import__('os').system('touch {marker}'))]<|tool_call_end|>"
    )
    tools = (
        DirectorToolSpec(
            name="run_mission",
            description="Execute a governed Aurora mission.",
            parameters={"type": "object"},
        ),
    )

    try:
        InferenceDirectorProvider._parse_tool_calls(content, tools)
    except RuntimeError as exc:
        assert "literal" in str(exc)
    else:
        raise AssertionError("non-literal native tool-call arguments must be rejected")
    assert not marker.exists()
