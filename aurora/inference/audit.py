"""Provider-neutral audit and telemetry contract for inference calls."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json

from .models import InferenceTelemetry


@dataclass(frozen=True)
class InferenceAuditEvent:
    request_id: str
    execution_id: str
    model: str
    provider: str
    success: bool
    started_at: str
    duration_ms: int
    error_code: str | None = None
    retryable: bool = False

    @classmethod
    def from_telemetry(cls, telemetry: InferenceTelemetry) -> "InferenceAuditEvent":
        return cls(
            request_id=telemetry.request_id,
            execution_id=telemetry.execution_id,
            model=telemetry.model,
            provider=telemetry.provider,
            success=telemetry.success,
            started_at=telemetry.started_at,
            duration_ms=telemetry.duration_ms,
            error_code=telemetry.error_code,
            retryable=telemetry.retryable,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
