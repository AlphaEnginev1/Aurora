from aurora.inference.audit import InferenceAuditEvent
from aurora.inference.errors import ProviderTimeoutError, ProviderErrorContext
from aurora.inference.models import (
    InferenceRequest,
    InferenceResponse,
    InferenceTelemetry,
    InferenceUsage,
)


def test_request_has_stable_request_identity():
    first = InferenceRequest(messages=[])
    second = InferenceRequest(messages=[])
    assert first.request_id
    assert second.request_id
    assert first.request_id != second.request_id


def test_response_contract_preserves_model_attribution_and_usage():
    request = InferenceRequest(messages=[], model="requested")
    telemetry = InferenceTelemetry(
        request_id=request.request_id,
        execution_id="exec-1",
        provider="fake",
        model="selected",
        started_at="2026-09-28T00:00:00+00:00",
        duration_ms=42,
        success=True,
    )
    response = InferenceResponse(
        model="selected",
        provider="fake",
        content="ok",
        request_id=request.request_id,
        execution_id="exec-1",
        requested_model=request.model,
        selected_model="selected",
        provider_model="provider/model",
        reported_model="provider/model",
        usage=InferenceUsage(prompt_tokens=3, completion_tokens=5, total_tokens=8),
        telemetry=telemetry,
    )
    assert response.request_id == request.request_id
    assert response.execution_id == "exec-1"
    assert response.requested_model == "requested"
    assert response.selected_model == "selected"
    assert response.provider_model == "provider/model"
    assert response.reported_model == "provider/model"
    assert response.usage.total_tokens == 8
    assert InferenceAuditEvent.from_telemetry(telemetry).execution_id == "exec-1"


def test_typed_provider_error_keeps_execution_identity():
    error = ProviderTimeoutError(
        "timed out",
        context=ProviderErrorContext(
            provider="fake",
            request_id="req-1",
            execution_id="exec-1",
            requested_model="requested",
            selected_model="selected",
            provider_model="provider/model",
        ),
    )
    assert error.code == "provider_timeout"
    assert error.retryable is True
    assert error.context.request_id == "req-1"
    assert error.context.execution_id == "exec-1"
    assert error.context.provider_model == "provider/model"
