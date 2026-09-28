"""Provider-neutral contracts for Aurora's human-facing Director role."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4


@dataclass(frozen=True)
class DirectorTool:
    """A capability Aurora exposes to a Director model as a declaration only."""

    name: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Director tool name must be non-empty")
        if not self.description.strip():
            raise ValueError("Director tool description must be non-empty")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("Director tool parameters must be a mapping")


@dataclass(frozen=True)
class DirectorToolCall:
    """A model-requested action. Aurora, not the model, executes it."""

    call_id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.call_id.strip():
            raise ValueError("Director tool call id must be non-empty")
        if not self.name.strip():
            raise ValueError("Director tool call name must be non-empty")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("Director tool call arguments must be a mapping")


@dataclass(frozen=True)
class DirectorToolResult:
    """Execution result returned by Aurora to the Director model."""

    call_id: str
    name: str
    content: str
    success: bool = True
    error_code: str | None = None


@dataclass(frozen=True)
class DirectorRequest:
    """Provider-neutral Director request; no concrete model is required."""

    messages: list[dict[str, Any]]
    model: str | None = None
    tools: tuple[DirectorTool, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class DirectorResponse:
    """Provider-neutral Director response with auditable provider/model identity."""

    provider: str
    model: str
    content: str = ""
    tool_calls: tuple[DirectorToolCall, ...] = ()
    request_id: str = ""
    execution_id: str = ""
    requested_model: str | None = None
    selected_model: str | None = None
    provider_model: str | None = None
    reported_model: str | None = None
    usage: Mapping[str, int | None] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None
