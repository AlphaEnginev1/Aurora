"""Command-line entry point for Aurora's durable 24/7 Director runtime."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from threading import Event, Thread
import signal

from .factory import build_director_runtime
from aurora.dashboard.conversation_api import DashboardConversationAPI
from aurora.dashboard.http_server import DashboardHTTPError, create_dashboard_server
from aurora.production_authorization import (
    ControlledLiveAuthorization,
    LiveAuthorizationError,
    REQUIRED_PREREQUISITES,
)
from aurora.operator_settings import OperatorSettingsStore, settings_path
from aurora.dashboard.provider_settings import ProviderSettingsService


def _path_env(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Aurora's 24/7 Director runtime")
    parser.add_argument(
        "--objective",
        help="Optional mission to submit before entering the continuous loop.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one runtime cycle and exit.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enter live mode only after explicit production authorization.",
    )
    parser.add_argument(
        "--live-operation",
        default="director_live_execution",
        help="Authorized live operation to activate.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Serve the Aurora dashboard and conversation API alongside the Director runtime.",
    )
    parser.add_argument(
        "--dashboard-host",
        default=os.getenv("AURORA_DASHBOARD_HOST", "127.0.0.1"),
        help="Dashboard bind host; non-loopback binds require AURORA_DASHBOARD_API_TOKEN.",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=int(os.getenv("AURORA_DASHBOARD_PORT", "8787")),
        help="Dashboard HTTP port.",
    )
    args = parser.parse_args()

    proxy_host = os.getenv("AURORA_EGRESS_PROXY_HOST")
    if not proxy_host:
        parser.error("AURORA_EGRESS_PROXY_HOST must be configured; direct egress is forbidden")

    if args.live:
        authorization_file = _path_env(
            "AURORA_LIVE_AUTHORIZATION_FILE",
            ".aurora-runtime/live-authorization.json",
        )
        canonical_commit = os.getenv("AURORA_CANONICAL_COMMIT", "").strip()
        try:
            record = ControlledLiveAuthorization.from_file(authorization_file)
            ControlledLiveAuthorization(
                expected_prerequisites=REQUIRED_PREREQUISITES,
                expected_canonical_commit=canonical_commit or None,
            ).require(
                record,
                operation=args.live_operation,
                canonical_commit=canonical_commit,
            )
        except LiveAuthorizationError as exc:
            parser.error(f"live authorization denied: {exc}")

    runtime = build_director_runtime(
        inference_policy_path=_path_env(
            "AURORA_INFERENCE_POLICY", "config/inference-policy.json"
        ),
        autonomy_policy_path=_path_env(
            "AURORA_AUTONOMY_POLICY", "config/autonomy-policy.json"
        ),
        registry_path=_path_env(
            "AURORA_TOOL_REGISTRY", "config/tool-registry.json"
        ),
        network_policy_path=_path_env(
            "AURORA_NETWORK_POLICY", "config/network-policy.json"
        ),
        workspace_root=_path_env(
            "AURORA_WORKSPACE_ROOT", ".aurora-runtime"
        ),
        egress_proxy_host=proxy_host,
        egress_proxy_port=int(os.getenv("AURORA_EGRESS_PROXY_PORT", "3128")),
        task_db_path=_path_env("AURORA_TASK_DB", ".aurora-runtime/tasks.db"),
        worker_id=os.getenv("AURORA_WORKER_ID"),
        worker_concurrency=int(os.getenv("AURORA_WORKER_CONCURRENCY", "4")),
        organization_path=_path_env(
            "AURORA_ORGANIZATION_POLICY", "config/organization.json"
        ),
        skills_path=_path_env(
            "AURORA_SKILLS_POLICY", "config/skills.json"
        ),
    )

    dashboard_server = None
    provider_settings = ProviderSettingsService(
        OperatorSettingsStore(settings_path(os.getenv("AURORA_WORKSPACE_ROOT", ".aurora-runtime"))),
        runtime,
    )
    if args.dashboard:
        try:
            if runtime.conversation_service is None:
                parser.error("Director conversation service is not configured")
            dashboard_api = DashboardConversationAPI(runtime.conversation_service)
            dashboard_server = create_dashboard_server(
                dashboard_gateway=runtime.dashboard_gateway,
                conversation_api=dashboard_api,
                host=args.dashboard_host,
                port=args.dashboard_port,
                api_token=os.getenv("AURORA_DASHBOARD_API_TOKEN"),
                provider_settings=provider_settings,
            )
        except DashboardHTTPError as exc:
            parser.error(str(exc))
        Thread(
            target=dashboard_server.serve_forever,
            name="aurora-dashboard",
            daemon=True,
        ).start()

    if args.objective:
        runtime.submit_mission(objective=args.objective)

    if args.once:
        runtime.run_once()
        if dashboard_server is not None:
            dashboard_server.shutdown()
            dashboard_server.server_close()
        return 0

    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        runtime.run_forever(stop)
    finally:
        if dashboard_server is not None:
            dashboard_server.shutdown()
            dashboard_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
