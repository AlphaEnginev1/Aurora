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