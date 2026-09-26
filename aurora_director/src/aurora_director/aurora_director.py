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

from aurora.orchestration.factory import build_orchestration_engine
from aurora.inference.router import ModelRoutingError
from aurora.inference.config import InferenceConfigError

logger = logging.getLogger(__name__)

class AuroraDirectorFunctionConfig(FunctionBaseConfig, name="aurora_director"):
    """Aurora Director configuration."""
    prefix: str = Field(default="AURORA", description="Prefix used in Director responses.")

@register_function(config_type=AuroraDirectorFunctionConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def aurora_director_function(config: AuroraDirectorFunctionConfig, builder: Builder):
    """Register the Aurora Director function."""
    async def _director(objective: str) -> str:
        objective = objective.strip()
        if not objective:
            return f"{config.prefix}\nMISSION_REJECTED\nReason: empty objective."
        if os.getenv("AURORA_ENABLE_INFERENCE", "0") != "1":
            return (
                f"{config.prefix}\nMISSION_ACCEPTED\n\nObjective:\n{objective}\n\n"
                "Initial orchestration plan:\n"
                "1. Analyse objective and constraints.\n"
                "2. Decompose objective into executable tasks.\n"
                "3. Select the appropriate specialist agents.\n"
                "4. Execute delegated tasks.\n"
                "5. Independently verify results.\n"
                "6. Consolidate verified results.\n"
                "7. Report status and remaining work.\n\n"
                "Inference Fabric: available but disabled by runtime policy."
            )
        policy_path = Path(os.getenv("AURORA_INFERENCE_POLICY", "config/inference-policy.json"))
        try:
            engine = build_orchestration_engine(policy_path)
            task_id = "director-" + hashlib.sha256(objective.encode("utf-8")).hexdigest()[:16]
            result = engine.run_task(task_id=task_id, role="director", objective=objective, capability="reasoning")
            return f"{config.prefix}\nMISSION_ACCEPTED\n\nObjective:\n{objective}\n\nModel-backed Director result:\n{result.response.content}"
        except (ModelRoutingError, InferenceConfigError) as exc:
            logger.warning("Inference routing unavailable: %s", exc)
            return f"{config.prefix}\nMISSION_ACCEPTED\n\nObjective:\n{objective}\n\nInference Fabric unavailable; deterministic orchestration retained.\nReason: {exc}"
    yield FunctionInfo.from_fn(_director, description=_director.__doc__)
