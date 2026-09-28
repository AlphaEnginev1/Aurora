"""Model-agnostic provider boundary for Aurora's Director role."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from aurora.inference.models import InferenceRequest, InferenceResponse
from aurora.inference.router import ModelRouter


@dataclass(frozen=True)
class DirectorToolSpec:
    """Provider-neutral declaration of a tool the Director may request."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class DirectorToolCall:
    """Unexecuted tool request returned by a Director provider."""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class DirectorModelResponse:
    """Minimal response contract required by planning and review."""

    content: str
    model: str
    provider: str
    request_id: str
    execution_id: str
    tool_calls: tuple[DirectorToolCall, ...] = ()


class DirectorProvider(Protocol):
    """Provider contract independent of Gemini, NVIDIA, or any vendor."""

    provider_id: str

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
        tools: tuple[DirectorToolSpec, ...] = (),
    ) -> DirectorModelResponse:
        ...


class InferenceDirectorProvider:
    """Compatibility adapter from Aurora's inference fabric to DirectorProvider."""

    provider_id = "inference_router"

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
    ) -> DirectorModelResponse:
        request = InferenceRequest(
            messages=messages,
            model=model,
            temperature=0.0,
            max_tokens=3000,
            metadata={"role": role, "director_tools": [tool.name for tool in tools]},
            request_id=uuid4().hex,
        )
        response: InferenceResponse = self._router.generate(
            request,
            capability="reasoning",
        )
        return DirectorModelResponse(
            content=response.content,
            model=response.model,
            provider=response.provider,
            request_id=response.request_id,
            execution_id=response.execution_id,
            tool_calls=(),
        )


__all__ = [
    "DirectorModelResponse",
    "DirectorToolCall",
    "DirectorToolSpec",
    "DirectorProvider",
    "InferenceDirectorProvider",
]
