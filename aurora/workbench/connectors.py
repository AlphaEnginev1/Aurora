"""Universal external connector registry and governed HTTP/API access."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse


class ConnectorError(RuntimeError):
    """Raised when a connector cannot be used safely."""


@dataclass(frozen=True)
class ConnectorSpec:
    connector_id: str
    connector_type: str
    base_url: str
    token_env: str | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    enabled: bool = True
    capabilities: tuple[str, ...] = ("read",)


class ConnectorRegistry:
    def __init__(self, path: str):
        self.path = path
        self._connectors = self._load()

    def _load(self) -> dict[str, ConnectorSpec]:
        try:
            data = json.loads(open(self.path, encoding="utf-8").read())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError(f"unable to load connector registry: {self.path}") from exc
        result: dict[str, ConnectorSpec] = {}
        for item in data.get("connectors", []):
            if not isinstance(item, dict):
                raise ConnectorError("connector entry must be an object")
            connector_id = str(item.get("connector_id", "")).strip()
            connector_type = str(item.get("connector_type", "")).strip()
            base_url = str(item.get("base_url", "")).strip()
            if not connector_id or not connector_type or not base_url:
                raise ConnectorError("connector requires connector_id, connector_type and base_url")
            parsed = urlparse(base_url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise ConnectorError(f"invalid connector base_url: {base_url}")
            capabilities = tuple(dict.fromkeys(str(value).strip() for value in item.get("capabilities", ["read"]) if str(value).strip()))
            result[connector_id] = ConnectorSpec(
                connector_id=connector_id,
                connector_type=connector_type,
                base_url=base_url.rstrip("/") + "/",
                token_env=str(item["token_env"]).strip() if item.get("token_env") else None,
                auth_header=str(item.get("auth_header", "Authorization")).strip(),
                auth_scheme=str(item.get("auth_scheme", "Bearer")).strip(),
                enabled=bool(item.get("enabled", True)),
                capabilities=capabilities or ("read",),
            )
        if len(result) != len(data.get("connectors", [])):
            raise ConnectorError("duplicate connector_id")
        return result

    def get(self, connector_id: str) -> ConnectorSpec:
        try:
            spec = self._connectors[connector_id]
        except KeyError as exc:
            raise ConnectorError(f"unknown connector: {connector_id}") from exc
        if not spec.enabled:
            raise ConnectorError(f"connector is disabled: {connector_id}")
        return spec

    def list(self) -> tuple[ConnectorSpec, ...]:
        return tuple(sorted(self._connectors.values(), key=lambda item: item.connector_id))


class ConnectorClient:
    def __init__(self, registry: ConnectorRegistry, gateway):
        self.registry = registry
        self.gateway = gateway

    def census(self) -> list[dict[str, Any]]:
        result = []
        for spec in self.registry.list():
            token_present = bool(spec.token_env and os.getenv(spec.token_env, "").strip())
            result.append({
                "connector_id": spec.connector_id,
                "connector_type": spec.connector_type,
                "base_url": spec.base_url,
                "enabled": spec.enabled,
                "credential_required": bool(spec.token_env),
                "token_configured": token_present,
                "capabilities": list(spec.capabilities),
            })
        return result

    def request(
        self,
        connector_id: str,
        *,
        path: str = "/",
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        capability: str = "read",
    ) -> Any:
        spec = self.registry.get(connector_id)
        if capability not in spec.capabilities:
            raise ConnectorError(
                f"connector capability denied: {connector_id}:{capability}"
            )
        if method.upper() != "GET" and "write" not in spec.capabilities and capability != "write":
            raise ConnectorError(f"connector write denied by connector scope: {connector_id}")
        relative = path.strip().lstrip("/")
        url = urljoin(spec.base_url, relative)
        parsed = urlparse(url)
        if parsed.hostname != urlparse(spec.base_url).hostname:
            raise ConnectorError("connector path escaped the registered host")
        headers = {"Accept": "application/json", "User-Agent": "Aurora-Workbench/1.0"}
        if spec.token_env:
            token = os.getenv(spec.token_env, "").strip()
            if not token:
                raise ConnectorError(f"connector credential is not configured: {spec.token_env}")
            headers[spec.auth_header] = (
                f"{spec.auth_scheme} {token}" if spec.auth_scheme else token
            )
        response = self.gateway.request_with_metadata(
            url,
            method=method.upper(),
            headers=headers,
            body=json.dumps(payload).encode("utf-8") if payload is not None else None,
        )
        raw = response.body.decode("utf-8")
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ConnectorError(f"connector response was not valid JSON: {connector_id}") from exc


__all__ = ["ConnectorClient", "ConnectorError", "ConnectorRegistry", "ConnectorSpec"]
