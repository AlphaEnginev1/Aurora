"""Provider-neutral inference contracts used by Aurora agents.

Increment 9A makes request/execution identity and provider telemetry part of the
common execution contract. Provider adapters must preserve these identities.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


def new_execution_id() -> str:
    return uuid4().hex


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    endpoint: str
    capabilities: tuple[str, ...] = ()
    context_window: int | None = None
    enabled: bool = True
    specialties: tuple[str, ...] = ()
    quality_score: float = 0.5
    latency_score: float = 0.5
    cost_score: float = 0.5
    healthy: bool = True
    available: bool = True
    pricing_tier: str = "unknown"


@dataclass(frozen=True)
class InferenceUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class InferenceTelemetry:
    request_id: str
    execution_id: str
    provider: str
    model: str
    started_at: str
    duration_ms: int
    success: bool
    error_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class InferenceRequest:
    messages: list[dict[str, Any]]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class InferenceResponse:
    model: str
    provider: str
    content: str
    raw: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    execution_id: str = ""
    requested_model: str | None = None
    selected_model: str | None = None
    provider_model: str | None = None
    reported_model: str | None = None
    usage: InferenceUsage = field(default_factory=InferenceUsage)
    telemetry: InferenceTelemetry | None = None
    routing_score: float | None = None
    routing_factors: dict[str, float] = field(default_factory=dict)
    routing_candidates: tuple[tuple[str, float], ...] = ()
