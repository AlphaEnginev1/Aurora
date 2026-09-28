"""Governed runtime for Aurora's model-facing Director boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import uuid4

from .models import (
    DirectorRequest,
    DirectorResponse,
    DirectorTool,
    DirectorToolCall,
    DirectorToolResult,
)
from .providers import DirectorProvider


@dataclass(frozen=True)
class DirectorExecution:
    """One Director turn plus any tool results produced by Aurora."""

    response: DirectorResponse
    tool_results: tuple[DirectorToolResult, ...] = ()


ToolHandler = Callable[[Mapping[str, Any]], Any]


class DirectorRuntime:
    """Execute Director turns while keeping tool authority inside Aurora."""

    def __init__(self, provider: DirectorProvider) -> None:
        self._provider = provider
        self._tools: dict[str, tuple[DirectorTool, ToolHandler]] = {}

    @property
    def provider_id(self) -> str:
        return self._provider.provider_id

    def register_tool(self, tool: DirectorTool, handler: ToolHandler) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Director tool already registered: {tool.name}")
        self._tools[tool.name] = (tool, handler)

    def run(self, request: DirectorRequest) -> DirectorExecution:
        execution_id = uuid4().hex
        declared_request = DirectorRequest(
            messages=request.messages,
            model=request.model,
            tools=tuple(tool for tool, _ in self._tools.values()),
            metadata=dict(request.metadata),
            request_id=request.request_id,
        )
        response = self._provider.generate(
            declared_request,
            execution_id=execution_id,
        )
        tool_results = self._execute_tool_calls(response.tool_calls)
        if not tool_results:
            return DirectorExecution(response=response)

        follow_up = self._provider.generate(
            DirectorRequest(
                messages=request.messages,
                model=request.model,
                tools=declared_request.tools,
                metadata=dict(request.metadata),
                request_id=request.request_id,
            ),
            execution_id=execution_id,
            tool_results=tool_results,
        )
        return DirectorExecution(response=follow_up, tool_results=tool_results)

    def _execute_tool_calls(
        self, calls: tuple[DirectorToolCall, ...]
    ) -> tuple[DirectorToolResult, ...]:
        results: list[DirectorToolResult] = []
        for call in calls:
            registered = self._tools.get(call.name)
            if registered is None:
                results.append(
                    DirectorToolResult(
                        call_id=call.call_id,
                        name=call.name,
                        content="unknown Director tool",
                        success=False,
                        error_code="DIRECTOR_TOOL_NOT_FOUND",
                    )
                )
                continue
            _, handler = registered
            try:
                value = handler(call.arguments)
                results.append(
                    DirectorToolResult(
                        call_id=call.call_id,
                        name=call.name,
                        content=str(value),
                        success=True,
                    )
                )
            except Exception as exc:
                results.append(
                    DirectorToolResult(
                        call_id=call.call_id,
                        name=call.name,
                        content="Director tool execution failed",
                        success=False,
                        error_code=type(exc).__name__,
                    )
                )
        return tuple(results)
