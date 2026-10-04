"""Client for Aurora local Windows/desktop Workbench companion."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class LocalComputerError(RuntimeError):
    """Raised when the local computer gateway cannot execute safely."""


class LocalComputerGateway:
    def __init__(self, *, url: str | None = None, token_env: str = "AURORA_LOCAL_GATEWAY_TOKEN", timeout: float = 20.0):
        self.url = (url or os.getenv("AURORA_LOCAL_GATEWAY_URL", "http://127.0.0.1:8790")).rstrip("/")
        self.token_env = token_env
        self.timeout = timeout

    def available(self) -> bool:
        return self.health()["ready"] is True

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", None)

    def execute(self, actions: list[dict[str, Any]], *, mission_id: str, actor: str) -> dict[str, Any]:
        if not actions or not isinstance(actions, list):
            raise LocalComputerError("at least one local computer action is required")
        if len(actions) > 40:
            raise LocalComputerError("local computer action limit exceeded")
        return self._request("POST", "/actions", {"actions": actions, "mission_id": mission_id, "actor": actor})

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "Aurora-Workbench/1.0"}
        token = os.getenv(self.token_env, "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, method=method, headers=headers, data=data)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(4_194_304)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise LocalComputerError(f"local gateway request failed: {exc}") from exc
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalComputerError("local gateway returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise LocalComputerError("local gateway response must be an object")
        if result.get("ok") is False:
            raise LocalComputerError(str(result.get("error") or "local gateway rejected request"))
        return result


__all__ = ["LocalComputerError", "LocalComputerGateway"]
