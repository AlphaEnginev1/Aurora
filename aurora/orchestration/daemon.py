"""Command-line entry point for Aurora's durable 24/7 Director runtime."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from threading import Event
import signal

from .factory import build_director_runtime


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
    args = parser.parse_args()

    proxy_host = os.getenv("AURORA_EGRESS_PROXY_HOST")
    if not proxy_host:
        parser.error("AURORA_EGRESS_PROXY_HOST must be configured; direct egress is forbidden")

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

    if args.objective:
        runtime.submit_mission(objective=args.objective)

    if args.once:
        runtime.run_once()
        return 0

    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    runtime.run_forever(stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
