from aurora.inference.models import InferenceResponse
from aurora.orchestration.director_provider import DirectorToolSpec, InferenceDirectorProvider


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
