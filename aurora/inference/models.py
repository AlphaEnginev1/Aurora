"""Provider-neutral inference contracts used by Aurora agents."""

from dataclasses import dataclass, field
from typing import Any

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

@dataclass(frozen=True)
class InferenceRequest:
    messages: list[dict[str, Any]]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class InferenceResponse:
    model: str
    provider: str
    content: str
    raw: dict[str, Any] = field(default_factory=dict)
