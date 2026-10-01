"""Dependency-free local HTTP boundary for the Aurora dashboard."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .conversation_api import DashboardConversationAPI, DashboardConversationAPIError
from .gateway import DashboardGateway, DashboardGatewayError


class DashboardHTTPError(RuntimeError):
    """Raised when the dashboard HTTP server cannot be configured safely."""


def bearer_token_authorizer(expected_token: str | None):
    """Return an authorizer that requires a bearer token when configured."""

    token = (expected_token or "").strip()

    def authorize(principal: str, request: dict[str, Any], provided_token: str | None = None) -> None:
        if token and provided_token != token:
            raise DashboardConversationAPIError("conversation authorization denied")

    return authorize


def create_dashboard_server(
    *,
    dashboard_gateway: DashboardGateway,
    conversation_api: DashboardConversationAPI,
    host: str = "127.0.0.1",
    port: int = 8787,
    ui_root: str | Path | None = None,
    api_token: str | None = None,
) -> ThreadingHTTPServer:
    _validate_bind(host, api_token)
    if port < 0 or port > 65535:
        raise DashboardHTTPError("dashboard port must be between 0 and 65535")
    root = Path(ui_root).resolve() if ui_root is not None else Path(__file__).resolve().parent / "ui"
    if not root.is_dir():
        raise DashboardHTTPError(f"dashboard UI root does not exist: {root}")

    class Handler(_DashboardHandler):
        pass

    Handler.dashboard_gateway = dashboard_gateway
    Handler.conversation_api = conversation_api
    Handler.ui_root = root
    Handler.api_token = (api_token or "").strip() or None

    return ThreadingHTTPServer((host, port), Handler)


class _DashboardHandler(BaseHTTPRequestHandler):
    dashboard_gateway: DashboardGateway
    conversation_api: DashboardConversationAPI
    ui_root: Path
    api_token: str | None

    server_version = "AuroraDashboard/1"

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/dashboard/snapshot":
                self._json(200, self.dashboard_gateway.snapshot())
                return
            if path.startswith("/api/dashboard/projects/"):
                project_id = unquote(path.rsplit("/", 1)[-1])
                self._json(200, self.dashboard_gateway.project(project_id))
                return
            if path.startswith("/api/dashboard/agents/"):
                role = unquote(path.rsplit("/", 1)[-1])
                self._json(200, self.dashboard_gateway.agent(role))
                return
            if path.startswith("/api/conversation/") and path.endswith("/turns"):
                session_id = unquote(path[len("/api/conversation/"):-len("/turns")].strip("/"))
                self._require_token()
                self._json(
                    200,
                    self.conversation_api.get_turns(
                        session_id=session_id,
                        principal=self._principal(),
                    ),
                )
                return
            if path in {"/", "/index.html"}:
                self._static("index.html")
                return
            self._static(path.lstrip("/"))
        except (DashboardGatewayError, DashboardConversationAPIError, DashboardHTTPError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": "internal dashboard error", "type": type(exc).__name__})

    def do_POST(self) -> None:
        try:
            self._require_token()
            parsed = urlparse(self.path)
            payload = self._json_body()
            if parsed.path == "/api/conversation/session":
                session_id = payload.get("session_id")
                if session_id is not None and not isinstance(session_id, str):
                    raise DashboardConversationAPIError("session_id must be a string")
                self._json(
                    201,
                    self.conversation_api.create_session(
                        session_id=session_id,
                        principal=self._principal(),
                    ),
                )
                return
            prefix = "/api/conversation/"
            if parsed.path.startswith(prefix) and parsed.path.endswith("/turn"):
                session_id = unquote(parsed.path[len(prefix):-len("/turn")].strip("/"))
                user_input = payload.get("user_input")
                if not isinstance(user_input, str):
                    raise DashboardConversationAPIError("user_input must be a string")
                model = payload.get("model")
                if model is not None and not isinstance(model, str):
                    raise DashboardConversationAPIError("model must be a string or null")
                self._json(
                    200,
                    self.conversation_api.send_turn(
                        session_id=session_id,
                        user_input=user_input,
                        model=model,
                        principal=self._principal(),
                    ),
                )
                return
            self._json(404, {"error": "dashboard route not found"})
        except (DashboardConversationAPIError, DashboardHTTPError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": "internal dashboard error", "type": type(exc).__name__})

    def _principal(self) -> str:
        return "dashboard"

    def _require_token(self) -> None:
        if self.api_token:
            authorization = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not authorization.startswith(prefix) or authorization[len(prefix):] != self.api_token:
                raise DashboardConversationAPIError("conversation authorization denied")

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DashboardConversationAPIError("invalid Content-Length") from exc
        if length < 0 or length > 1_048_576:
            raise DashboardConversationAPIError("request body is too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DashboardConversationAPIError("invalid JSON body") from exc
        if not isinstance(data, dict):
            raise DashboardConversationAPIError("request JSON must be an object")
        return data

    def _json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self, relative: str) -> None:
        candidate = (self.ui_root / relative).resolve()
        try:
            candidate.relative_to(self.ui_root)
        except ValueError as exc:
            raise DashboardHTTPError("invalid dashboard path") from exc
        if not candidate.is_file():
            self._json(404, {"error": "dashboard asset not found"})
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }.get(candidate.suffix.lower(), "application/octet-stream")
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        # Keep dashboard request logging quiet; Aurora observability owns durable telemetry.
        return


def _validate_bind(host: str, token: str | None) -> None:
    normalized = host.strip().lower()
    loopback = normalized in {"127.0.0.1", "::1", "localhost"}
    if not loopback and not (token or "").strip():
        raise DashboardHTTPError(
            "non-loopback dashboard bind requires AURORA_DASHBOARD_API_TOKEN"
        )


__all__ = ["DashboardHTTPError", "bearer_token_authorizer", "create_dashboard_server"]
