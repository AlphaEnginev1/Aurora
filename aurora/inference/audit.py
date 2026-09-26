"""Small provider-neutral audit event contract for inference calls."""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json

@dataclass(frozen=True)
class InferenceAuditEvent:
    model: str
    provider: str
    success: bool
    started_at: str
    duration_ms: int
    error_type: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
