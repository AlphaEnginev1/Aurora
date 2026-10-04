"""Governed MCP client bridge for Aurora Universal Workbench."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any


class MCPBridgeError(RuntimeError):
    """Raised when an MCP server cannot be safely used."""


@dataclass(frozen=True)
class MCPServerSpec:
    server_id: str
    transport: str
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    header_env: dict[str, str] = None
    env_passthrough: tuple[str, ...] = ()
    enabled: bool = True


class MCPRegistry:
    """Static operator-managed MCP server catalog; secrets stay in the environment."""

    def __init__(self, path: str):
        self.path = path
        self._servers = self._load()

    def _load(self) -> dict[str, MCPServerSpec]:
        try:
            data = json.loads(open(self.path, encoding="utf-8").read())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MCPBridgeError(f"unable to load MCP registry: {self.path}") from exc
        result: dict[str, MCPServerSpec] = {}
        for item in data.get("servers", []):
            if not isinstance(item, dict):
                raise MCPBridgeError("MCP server entry must be an object")
            server_id = str(item.get("server_id", "")).strip()
            transport = str(item.get("transport", "")).strip().lower()
            if not server_id or transport not in {"streamable-http", "stdio"}:
                raise MCPBridgeError("MCP server requires server_id and transport=streamable-http|stdio")
            spec = MCPServerSpec(
                server_id=server_id,
                transport=transport,
                url=(str(item["url"]).strip() if item.get("url") else None),
                command=(str(item["command"]).strip() if item.get("command") else None),
                args=tuple(str(value) for value in item.get("args", [])),
                cwd=(str(item["cwd"]).strip() if item.get("cwd") else None),
                header_env={str(k): str(v) for k, v in (item.get("header_env") or {}).items()},
                env_passthrough=tuple(str(value) for value in item.get("env_passthrough", [])),
                enabled=bool(item.get("enabled", True)),
            )
            if server_id in result:
                raise MCPBridgeError(f"duplicate MCP server: {server_id}")
            result[server_id] = spec
        return result

    def list(self) -> tuple[MCPServerSpec, ...]:
        return tuple(sorted(self._servers.values(), key=lambda item: item.server_id))

    def get(self, server_id: str) -> MCPServerSpec:
        try:
            spec = self._servers[server_id]
        except KeyError as exc:
            raise MCPBridgeError(f"unknown MCP server: {server_id}") from exc
        if not spec.enabled:
            raise MCPBridgeError(f"MCP server is disabled: {server_id}")
        return spec


class MCPClientBridge:
    """Connect to MCP servers over stdio or Streamable HTTP and expose typed tool calls."""

    def __init__(self, registry: MCPRegistry, *, timeout_seconds: float = 30.0):
        self.registry = registry
        self.timeout_seconds = timeout_seconds

    def list_tools(self, server_id: str) -> list[dict[str, Any]]:
        import anyio
        return anyio.run(self._list_tools, server_id)

    def call_tool(self, server_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        import anyio
        return anyio.run(self._call_tool, server_id, name, arguments)

    async def _list_tools(self, server_id: str) -> list[dict[str, Any]]:
        spec = self.registry.get(server_id)
        async with self._client(spec) as client:
            tools: list[dict[str, Any]] = []
            cursor = None
            while True:
                page = await client.list_tools(cursor=cursor)
                for tool in page.tools:
                    tools.append({
                        "name": tool.name,
                        "title": getattr(tool, "title", None),
                        "description": getattr(tool, "description", None),
                        "input_schema": getattr(tool, "input_schema", {}),
                    })
                cursor = page.next_cursor
                if cursor is None:
                    break
            return tools

    async def _call_tool(self, server_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self.registry.get(server_id)
        if not name.strip():
            raise MCPBridgeError("MCP tool name is required")
        if not isinstance(arguments, dict):
            raise MCPBridgeError("MCP tool arguments must be an object")
        async with self._client(spec) as client:
            result = await client.call_tool(name, arguments, read_timeout_seconds=self.timeout_seconds)
            content: list[dict[str, Any]] = []
            for block in result.content:
                item: dict[str, Any] = {"type": getattr(block, "type", "unknown")}
                for key in ("text", "mime_type", "data", "uri", "name"):
                    value = getattr(block, key, None)
                    if value is not None:
                        item[key] = value
                content.append(item)
            return {
                "server_id": server_id,
                "tool": name,
                "is_error": bool(result.is_error),
                "structured_content": result.structured_content,
                "content": content,
                "protocol_version": client.protocol_version,
            }

    @asynccontextmanager
    async def _client(self, spec: MCPServerSpec):
        try:
            from mcp import Client
            from mcp.client.stdio import StdioServerParameters
        except ImportError as exc:
            raise MCPBridgeError("MCP SDK is not installed") from exc

        if spec.transport == "stdio":
            if not spec.command:
                raise MCPBridgeError(f"MCP stdio server has no command: {spec.server_id}")
            env = {}
            for name in spec.env_passthrough:
                if name in os.environ:
                    env[name] = os.environ[name]
            params = StdioServerParameters(command=spec.command, args=list(spec.args), env=env, cwd=spec.cwd)
            async with Client(params) as client:
                yield client
            return

        if not spec.url:
            raise MCPBridgeError(f"MCP HTTP server has no URL: {spec.server_id}")
        try:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise MCPBridgeError("MCP HTTP transport dependencies are not installed") from exc
        headers = {}
        for header_name, env_name in (spec.header_env or {}).items():
            value = os.getenv(env_name, "")
            if value:
                headers[header_name] = value
        timeout = httpx2.Timeout(self.timeout_seconds, read=self.timeout_seconds * 10)
        async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http_client:
            async with streamable_http_client(spec.url, http_client=http_client) as transport:
                async with Client(transport) as client:
                    yield client


__all__ = ["MCPBridgeError", "MCPClientBridge", "MCPRegistry", "MCPServerSpec"]
