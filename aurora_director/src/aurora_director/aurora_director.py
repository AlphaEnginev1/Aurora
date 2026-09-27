import hashlib
import logging
import os
from pathlib import Path

from pydantic import Field

from nat.plugin_api import Builder
from nat.plugin_api import FunctionBaseConfig
from nat.plugin_api import FunctionInfo
from nat.plugin_api import LLMFrameworkEnum
from nat.plugin_api import register_function

from aurora.orchestration.director import DirectorPlanError, DirectorPlanner
from aurora.orchestration.factory import build_director_service
from aurora.inference.config import InferenceConfigError, load_model_specs
from aurora.inference.models import InferenceRequest
from aurora.inference.router import ModelRouter, ModelRoutingError

logger = logging.getLogger(__name__)


class AuroraDirectorFunctionConfig(FunctionBaseConfig, name="aurora_director"):
    """Aurora Director configuration."""
    prefix: str = Field(default="AURORA", description="Prefix used in Director responses.")


def _format_plan(plan) -> str:
    lines = [
        "Structured Director plan:",
        f"Model: {plan.model}",
    ]
    for task in plan.tasks:
        capability = task.capability or "reasoning"
        deps = ", ".join(task.depends_on) or "none"
        lines.append(
            f"- {task.task_id} [{task.role}] capability={capability} "
            f"depends_on={deps}: {task.objective}"
        )
    return "\n".join(lines)


def _format_mission(result) -> str:
    lines = [_format_plan(result.plan), "", "Execution:"]
    for item in result.tasks:
        detail = item.error or "completed"
        lines.append(f"- {item.task.task_id}: {item.status.value} ({detail})")
    return "\n".join(lines)


@register_function(
    config_type=AuroraDirectorFunctionConfig,
    framework_wrappers=[LLMFrameworkEnum.LANGCHAIN],
)
async def aurora_director_function(config: AuroraDirectorFunctionConfig, builder: Builder):
    """Register the Aurora Director function."""

    async def _director(objective: str) -> str:
        objective = objective.strip()
        if not objective:
            return f"{config.prefix}\nMISSION_REJECTED\nReason: empty objective."

        if os.getenv("AURORA_ENABLE_INFERENCE", "0") != "1":
            return (
                f"{config.prefix}\nMISSION_ACCEPTED\n\nObjective:\n{objective}\n\n"
                "Inference is disabled by runtime policy; no autonomous task execution was started."
            )

        inference_policy = Path(
            os.getenv("AURORA_INFERENCE_POLICY", "config/inference-policy.json")
        )

        try:
            models = load_model_specs(inference_policy)
            router = ModelRouter(
                models=models,
                nvidia_api_key=os.getenv("NVIDIA_API_KEY"),
            )
            planner = DirectorPlanner(router)
            plan = planner.plan(objective=objective)

            if os.getenv("AURORA_ENABLE_AUTONOMY_EXECUTION", "0") != "1":
                return (
                    f"{config.prefix}\nMISSION_ACCEPTED\n\nObjective:\n{objective}\n\n"
                    + _format_plan(plan)
                    + "\n\nAutonomous execution is disabled by runtime policy."
                )

            proxy_host = os.getenv("AURORA_EGRESS_PROXY_HOST")
            if not proxy_host:
                return (
                    f"{config.prefix}\nMISSION_ACCEPTED\n\nObjective:\n{objective}\n\n"
                    + _format_plan(plan)
                    + "\n\nMISSION_BLOCKED\nReason: controlled egress proxy host is not configured; "
                    "Aurora will not fall back to direct package downloads."
                )

            service = build_director_service(
                inference_policy_path=inference_policy,
                autonomy_policy_path=Path(
                    os.getenv("AURORA_AUTONOMY_POLICY", "config/autonomy-policy.json")
                ),
                registry_path=Path(
                    os.getenv("AURORA_TOOL_REGISTRY", "config/tool-registry.json")
                ),
                network_policy_path=Path(
                    os.getenv("AURORA_NETWORK_POLICY", "config/network-policy.json")
                ),
                workspace_root=Path(
                    os.getenv("AURORA_WORKSPACE_ROOT", ".aurora-runtime")
                ),
                egress_proxy_host=proxy_host,
                egress_proxy_port=int(os.getenv("AURORA_EGRESS_PROXY_PORT", "3128")),
            )
            mission = service.run_mission(objective=objective)
            return f"{config.prefix}\nMISSION_ACCEPTED\n\nObjective:\n{objective}\n\n{_format_mission(mission)}"

        except (ModelRoutingError, InferenceConfigError, DirectorPlanError, ValueError, OSError) as exc:
            logger.warning("Aurora Director unavailable: %s", exc)
            return (
                f"{config.prefix}\nMISSION_ACCEPTED\n\nObjective:\n{objective}\n\n"
                f"MISSION_BLOCKED\nReason: {exc}"
            )

    yield FunctionInfo.from_fn(_director, description=_director.__doc__)
