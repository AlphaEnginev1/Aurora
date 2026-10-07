"""Model-agnostic provider boundary for Aurora's Director role."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
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
    """Provider contract independent of any vendor."""

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
    _TEXT_TOOL_CALL_RE = re.compile(
        r"^\[(?P<name>[A-Za-z0-9_.:-]+)(?P<meta>[^\]]*)\]\s*"
        r"(?P<body>.*?)\s*\[/\1\]$",
        re.DOTALL,
    )
    _TASK_RE = re.compile(r'^task\s*:\s*"(?P<objective>.*?)"\s*
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
        tools: tuple[DirectorToolSpec, ...] = (),
    ) -> DirectorModelResponse:
        prompt_messages = list(messages)
        if tools:
            instruction = self._tool_instruction(tools)
            if prompt_messages and prompt_messages[0].get("role") == "system":
                prompt_messages[0] = {
                    **prompt_messages[0],
                    "content": str(prompt_messages[0].get("content", "")) + "\n\n" + instruction,
                }
            else:
                prompt_messages.insert(0, {"role": "system", "content": instruction})
        request = InferenceRequest(
            messages=prompt_messages,
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
        tool_calls = self._parse_tool_calls(response.content, tools)
        return DirectorModelResponse(
            content=response.content if not tool_calls else "",
            model=response.model,
            provider=response.provider,
            request_id=response.request_id,
            execution_id=response.execution_id,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _tool_instruction(tools: tuple[DirectorToolSpec, ...]) -> str:
        declarations = "\n".join(
            f"- {tool.name}: {tool.description}" for tool in tools
        )
        return (
            "Tool mode is enabled. When actual execution is required, return ONLY a JSON object "
            "with this shape: {\"tool_calls\":[{\"name\":\"tool_name\",\"arguments\":{}}]}. "
            "Do not execute tools yourself. When no execution is required, answer normally. "
            "Available tools:\n" + declarations
        )

    @classmethod
    def _parse_tool_calls(
        cls,
        content: str,
        tools: tuple[DirectorToolSpec, ...],
    ) -> tuple[DirectorToolCall, ...]:
        if not tools:
            return ()
        raw = content.strip()
        fence = chr(96) * 3
        if raw.startswith(fence):
            lines = raw.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith(fence):
                raw = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("tool_calls"), list):
            return cls._build_tool_calls(payload["tool_calls"], tools)
        return cls._parse_structured_text_tool_call(raw, tools)

    @classmethod
    def _parse_structured_text_tool_call(
        cls,
        raw: str,
        tools: tuple[DirectorToolSpec, ...],
    ) -> tuple[DirectorToolCall, ...]:
        match = cls._TEXT_TOOL_CALL_RE.fullmatch(raw)
        if match is None:
            return ()
        name = match.group("name")
        known = {tool.name for tool in tools}
        if name not in known:
            raise RuntimeError(f"Director requested unregistered tool: {name}")
        objective = None
        for line in match.group("body").splitlines():
            line = line.strip()
            task_match = cls._TASK_RE.fullmatch(line)
            if task_match:
                objective = task_match.group("objective").strip()
                break
        if not objective:
            raise RuntimeError(f"Director tool call {name} is missing a non-empty task")
        return (DirectorToolCall(
            call_id=uuid4().hex,
            name=name,
            arguments={"objective": objective},
        ),)

    @staticmethod
    def _build_tool_calls(
        raw_calls: list[Any],
        tools: tuple[DirectorToolSpec, ...],
    ) -> tuple[DirectorToolCall, ...]:
        known = {tool.name for tool in tools}
        calls: list[DirectorToolCall] = []
        for item in raw_calls:
            if not isinstance(item, dict):
                raise RuntimeError("Director tool call item must be an object")
            name = item.get("name")
            arguments = item.get("arguments", {})
            if not isinstance(name, str) or name not in known:
                raise RuntimeError(f"Director requested unregistered tool: {name}")
            if not isinstance(arguments, dict):
                raise RuntimeError("Director tool call arguments must be an object")
            calls.append(DirectorToolCall(
                call_id=uuid4().hex,
                name=name,
                arguments=arguments,
            ))
        return tuple(calls)



__all__ = [
    "DirectorModelResponse",
    "DirectorToolCall",
    "DirectorToolSpec",
    "DirectorProvider",
    "InferenceDirectorProvider",
]
, re.DOTALL)

    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
        tools: tuple[DirectorToolSpec, ...] = (),
    ) -> DirectorModelResponse:
        prompt_messages = list(messages)
        if tools:
            instruction = self._tool_instruction(tools)
            if prompt_messages and prompt_messages[0].get("role") == "system":
                prompt_messages[0] = {
                    **prompt_messages[0],
                    "content": str(prompt_messages[0].get("content", "")) + "\n\n" + instruction,
                }
            else:
                prompt_messages.insert(0, {"role": "system", "content": instruction})
        request = InferenceRequest(
            messages=prompt_messages,
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
        tool_calls = self._parse_tool_calls(response.content, tools)
        return DirectorModelResponse(
            content=response.content if not tool_calls else "",
            model=response.model,
            provider=response.provider,
            request_id=response.request_id,
            execution_id=response.execution_id,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _tool_instruction(tools: tuple[DirectorToolSpec, ...]) -> str:
        declarations = "\n".join(
            f"- {tool.name}: {tool.description}" for tool in tools
        )
        return (
            "Tool mode is enabled. When actual execution is required, return ONLY a JSON object "
            "with this shape: {\"tool_calls\":[{\"name\":\"tool_name\",\"arguments\":{}}]}. "
            "Do not execute tools yourself. When no execution is required, answer normally. "
            "Available tools:\n" + declarations
        )

    @staticmethod
    def _parse_tool_calls(
        content: str,
        tools: tuple[DirectorToolSpec, ...],
    ) -> tuple[DirectorToolCall, ...]:
        if not tools:
            return ()
        raw = content.strip()
        fence = "```"
        if raw.startswith(fence):
            lines = raw.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith(fence):
                raw = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        if not isinstance(payload, dict) or not isinstance(payload.get("tool_calls"), list):
            return ()
        known = {tool.name for tool in tools}
        calls: list[DirectorToolCall] = []
        for item in payload["tool_calls"]:
            if not isinstance(item, dict):
                raise RuntimeError("Director tool call item must be an object")
            name = item.get("name")
            arguments = item.get("arguments", {})
            if not isinstance(name, str) or name not in known:
                raise RuntimeError(f"Director requested unregistered tool: {name}")
            if not isinstance(arguments, dict):
                raise RuntimeError("Director tool call arguments must be an object")
            calls.append(DirectorToolCall(
                call_id=uuid4().hex,
                name=name,
                arguments=arguments,
            ))
        return tuple(calls)


__all__ = [
    "DirectorModelResponse",
    "DirectorToolCall",
    "DirectorToolSpec",
    "DirectorProvider",
    "InferenceDirectorProvider",
]
