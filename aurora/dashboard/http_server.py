"""Dependency-free local HTTP boundary for the Aurora dashboard."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import uuid
from typing import Any
from urllib.parse import unquote, urlparse

from .conversation_api import DashboardConversationAPI, DashboardConversationAPIError
from .gateway import DashboardGateway, DashboardGatewayError
from .provider_settings import DashboardProviderSettingsError, ProviderSettingsService
from .update_service import DashboardUpdateError, DashboardUpdateService
from .health import DashboardHealthService
from aurora.workbench.github_auth import GitHubOAuthError, GitHubOAuthService


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
    provider_settings: ProviderSettingsService | None = None,
    update_service: DashboardUpdateService | None = None,
    health_service: DashboardHealthService | None = None,
    workbench=None,
    github_oauth: GitHubOAuthService | None = None,
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
    Handler.provider_settings = provider_settings
    Handler.update_service = update_service
    Handler.health_service = health_service
    Handler.workbench = workbench
    Handler.github_oauth = github_oauth

    return ThreadingHTTPServer((host, port), Handler)


class _DashboardHandler(BaseHTTPRequestHandler):
    dashboard_gateway: DashboardGateway
    conversation_api: DashboardConversationAPI
    ui_root: Path
    api_token: str | None
    provider_settings: ProviderSettingsService | None
    update_service: DashboardUpdateService | None
    health_service: DashboardHealthService | None
    workbench: Any | None
    github_oauth: GitHubOAuthService | None

    server_version = "AuroraDashboard/1"

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/dashboard/snapshot":
                self._json(200, self.dashboard_gateway.snapshot())
                return
            if path == "/api/dashboard/update/status":
                self._require_token()
                if self.update_service is None:
                    raise DashboardHTTPError("dashboard update service is not configured")
                self._json(200, self.update_service.status())
                return
            if path == "/api/dashboard/health":
                self._require_token()
                if self.health_service is None:
                    raise DashboardHTTPError("dashboard health service is not configured")
                self._json(200, self.health_service.health())
                return
            if path == "/api/github/status":
                self._json(200, self._github_oauth().public())
                return
            if path == "/api/github/oauth/start":
                location = self._github_oauth().authorization_url()
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()
                return
            if path == "/api/github/oauth/callback":
                query = parsed.query
                params = {}
                for item in query.split("&"):
                    if not item:
                        continue
                    key, _, value = item.partition("=")
                    params[unquote(key)] = unquote(value)
                result = self._github_oauth().callback(params.get("code", ""), params.get("state", ""))
                self._html_redirect("/?github=connected&login=" + quote(str(result.get("login") or "")))
                return
            if path == "/api/github/disconnect":
                self._require_token()
                self._github_oauth().disconnect()
                self._json(200, {"connected": False})
                return
            if path == "/api/dashboard/settings":
                self._require_token()
                if self.provider_settings is None:
                    raise DashboardHTTPError("provider settings are not configured")
                self._json(200, self.provider_settings.public())
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
        except (DashboardGatewayError, DashboardConversationAPIError, DashboardHTTPError, DashboardProviderSettingsError, DashboardUpdateError, GitHubOAuthError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": "internal dashboard error", "type": type(exc).__name__})

    def do_POST(self) -> None:
        try:
            self._require_token()
            parsed = urlparse(self.path)
            if parsed.path == "/api/dashboard/artifacts":
                if self.workbench is None:
                    raise DashboardHTTPError("dashboard Workbench is not configured")
                filename = self.headers.get("X-Aurora-Filename", "").strip()
                if not filename:
                    raise DashboardConversationAPIError("X-Aurora-Filename is required")
                filename = unquote(filename)
                safe_name = Path(filename).name
                if not safe_name or safe_name in {".", ".."} or len(safe_name) > 180:
                    raise DashboardConversationAPIError("invalid upload filename")
                content_length = int(self.headers.get("Content-Length", "0"))
                max_bytes = 25 * 1024 * 1024
                if content_length <= 0 or content_length > max_bytes:
                    raise DashboardConversationAPIError("upload must be between 1 byte and 25 MiB")
                uploads = self.workbench.runtime_root / "uploads"
                uploads.mkdir(parents=True, exist_ok=True)
                destination = uploads / f"{uuid.uuid4().hex}-{re.sub(r'[^A-Za-z0-9._-]', '_', safe_name)}"
                data = self.rfile.read(content_length)
                if len(data) != content_length:
                    raise DashboardConversationAPIError("upload body was incomplete")
                destination.write_bytes(data)
                mission_id = self.headers.get("X-Aurora-Mission-Id", "").strip() or f"dashboard-upload-{uuid.uuid4().hex}"
                result = self.workbench.execute(
                    __import__("aurora.workbench", fromlist=["WorkbenchRequest"]).WorkbenchRequest(
                        capability="workbench:artifact:intake",
                        actor="dashboard",
                        mission_id=mission_id,
                        arguments=(str(destination),),
                        options={"work_id": mission_id},
                    )
                )
                self._json(201, {"artifact": result.output, "mission_id": mission_id})
                return
            payload = self._json_body()
            if parsed.path == "/api/dashboard/update":
                if self.update_service is None:
                    raise DashboardHTTPError("dashboard update service is not configured")
                self._json(202, self.update_service.request_update())
                return
            if parsed.path == "/api/dashboard/settings":
                if self.provider_settings is None:
                    raise DashboardHTTPError("provider settings are not configured")
                self._json(200, self.provider_settings.save_and_apply(payload))
                return
            if parsed.path == "/api/dashboard/settings/test":
                if self.provider_settings is None:
                    raise DashboardHTTPError("provider settings are not configured")
                provider = payload.get("provider")
                if not isinstance(provider, str):
                    raise DashboardConversationAPIError("provider must be a string")
                self._json(200, self.provider_settings.test_provider(provider))
                return
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
        except (DashboardConversationAPIError, DashboardHTTPError, DashboardProviderSettingsError, DashboardUpdateError, GitHubOAuthError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": "internal dashboard error", "type": type(exc).__name__})

    def _github_oauth(self) -> GitHubOAuthService:
        service = getattr(self, "github_oauth", None)
        if service is None:
            raise DashboardHTTPError("GitHub account connection is not configured")
        return service

    def _html_redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

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
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
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
