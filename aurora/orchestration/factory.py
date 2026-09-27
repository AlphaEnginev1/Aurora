"""Construct Aurora orchestration and autonomous Director services."""

from __future__ import annotations

import json
import os
from pathlib import Path

from aurora.autonomy.acquisition import AcquisitionManager
from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.capability_runtime import CapabilityRuntime
from aurora.autonomy.internet import InternetGateway, InternetPolicy
from aurora.autonomy.policy import Policy
from aurora.autonomy.registry import load_registry
from aurora.autonomy.sandbox import SandboxExecutor, SandboxPolicy
from aurora.inference.config import load_model_specs
from aurora.inference.router import ModelRouter
from .director import DirectorPlanner, DirectorService
from .engine import OrchestrationEngine
from .task_engine import TaskEngine


def build_orchestration_engine(policy_path: str | Path) -> OrchestrationEngine:
    """Build model routing without storing provider credentials."""
    models = load_model_specs(policy_path)
    return OrchestrationEngine(
        ModelRouter(
            models=models,
            nvidia_api_key=os.getenv("NVIDIA_API_KEY"),
        )
    )


def build_director_service(
    *,
    inference_policy_path: str | Path,
    autonomy_policy_path: str | Path,
    registry_path: str | Path,
    network_policy_path: str | Path,
    workspace_root: str | Path,
    egress_proxy_host: str,
    egress_proxy_port: int = 3128,
) -> DirectorService:
    """Build the complete Director -> tasks -> capabilities execution path.

    The capability runtime intentionally requires an explicit egress proxy host.
    This prevents accidental fallback to host-direct package downloads.
    """

    inference_specs = load_model_specs(inference_policy_path)
    router = ModelRouter(
        models=inference_specs,
        nvidia_api_key=os.getenv("NVIDIA_API_KEY"),
    )

    autonomy_data = json.loads(Path(autonomy_policy_path).read_text(encoding="utf-8"))
    policy = Policy(
        allowed_capabilities=frozenset(autonomy_data.get("allowed_capabilities", [])),
        allowed_sources=frozenset(autonomy_data.get("allowed_sources", [])),
        require_verification=bool(autonomy_data.get("require_verification", True)),
    )
    registry = load_registry(registry_path)

    network_policy = InternetPolicy.from_file(network_policy_path)
    gateway = InternetGateway(network_policy)

    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    install_root = root / "managed-site"
    transaction_dir = root / "transactions"
    audit_path = root / "audit.jsonl"

    sandbox_policy = SandboxPolicy(
        network=False,
        timeout_seconds=120,
        cpu_seconds=60,
        memory_bytes=1_073_741_824,
        max_processes=64,
        max_open_files=256,
        max_output_bytes=1_048_576,
        allowed_env=frozenset({"PATH", "LANG", "LC_ALL", "TZ"}),
        readonly_system=True,
    )
    offline_sandbox = SandboxExecutor(sandbox_policy)
    network_sandbox = SandboxExecutor(
        SandboxPolicy(
            network=True,
            egress_proxy_host=egress_proxy_host,
            egress_proxy_port=egress_proxy_port,
            timeout_seconds=120,
            cpu_seconds=60,
            memory_bytes=1_073_741_824,
            max_processes=64,
            max_open_files=256,
            max_output_bytes=1_048_576,
            allowed_env=frozenset({"PATH", "LANG", "LC_ALL", "TZ"}),
            readonly_system=True,
        )
    )

    acquisition = AcquisitionManager(
        allowed_sources=policy.allowed_sources,
        audit_path=audit_path,
        install_root=install_root,
        transaction_dir=transaction_dir,
        internet_gateway=gateway,
        sandbox=offline_sandbox,
        network_sandbox=network_sandbox,
    )
    broker = CapabilityBroker(
        policy=policy,
        registry=registry,
        acquisition=acquisition,
    )
    capability_runtime = CapabilityRuntime(
        broker,
        offline_sandbox,
        audit_path=audit_path,
    )
    orchestration = OrchestrationEngine(
        router,
        capability_runtime=capability_runtime,
    )
    task_engine = TaskEngine(orchestration, root)
    planner = DirectorPlanner(router)

    return DirectorService(planner, task_engine)
