from aurora.director import (
    DirectorRequest,
    DirectorResponse,
    DirectorTool,
    DirectorToolCall,
    DirectorToolResult,
)
from aurora.director.providers import DirectorProvider


class FakeDirectorProvider:
    provider_id = "fake_director"

    def generate(self, request, *, execution_id, tool_results=()):
        return DirectorResponse(
            provider=self.provider_id,
            model=request.model or "fake/director",
            content="Aurora Director response",
            tool_calls=(
                DirectorToolCall(
                    call_id="call-1",
                    name="create_task",
                    arguments={"objective": "research"},
                ),
            ) if not tool_results else (),
            request_id=request.request_id,
            execution_id=execution_id,
            requested_model=request.model,
            selected_model=request.model or "fake/director",
            provider_model=request.model or "fake/director",
            reported_model=request.model or "fake/director",
        )


def test_director_contract_preserves_identity_and_tool_boundary():
    request = DirectorRequest(
        messages=[{"role": "user", "content": "Research this."}],
        model="gemini-3.7-flash",
        tools=(
            DirectorTool(
                name="create_task",
                description="Create a governed Aurora task.",
                parameters={"type": "object", "properties": {"objective": {"type": "string"}}},
            ),
        ),
    )
    provider: DirectorProvider = FakeDirectorProvider()
    response = provider.generate(request, execution_id="director-exec-1")

    assert response.request_id == request.request_id
    assert response.execution_id == "director-exec-1"
    assert response.provider == "fake_director"
    assert response.model == "gemini-3.7-flash"
    assert response.provider_model == "gemini-3.7-flash"
    assert response.tool_calls[0].name == "create_task"


def test_director_tool_result_is_data_not_execution_authority():
    result = DirectorToolResult(
        call_id="call-1",
        name="create_task",
        content='{"task_id":"t1"}',
        success=True,
    )
    assert result.call_id == "call-1"
    assert result.success is True


def test_director_request_does_not_require_a_concrete_model():
    request = DirectorRequest(messages=[{"role": "user", "content": "Hello Aurora"}])
    assert request.model is None


def test_invalid_tool_contract_is_rejected():
    try:
        DirectorTool(name="", description="invalid")
    except ValueError as exc:
        assert "name" in str(exc)
    else:
        raise AssertionError("empty Director tool name must be rejected")
