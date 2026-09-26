import logging

from pydantic import Field

from nat.plugin_api import Builder
from nat.plugin_api import FunctionBaseConfig
from nat.plugin_api import FunctionInfo
from nat.plugin_api import LLMFrameworkEnum
from nat.plugin_api import register_function

logger = logging.getLogger(__name__)


class AuroraDirectorFunctionConfig(
    FunctionBaseConfig,
    name="aurora_director",
):
    """
    Aurora Director IA.

    Orquestador principal de Aurora. Recibe un objetivo humano,
    lo estructura como una misión y devuelve un plan inicial
    para su posterior delegación a agentes especializados.
    """

    prefix: str = Field(
        default="AURORA",
        description="Prefix used in Director responses.",
    )


@register_function(
    config_type=AuroraDirectorFunctionConfig,
    framework_wrappers=[LLMFrameworkEnum.LANGCHAIN],
)
async def aurora_director_function(
    config: AuroraDirectorFunctionConfig,
    builder: Builder,
):
    """
    Register the Aurora Director function.

    The first implementation is intentionally deterministic.
    It establishes the orchestration boundary before adding
    external LLM providers and specialist agents.
    """

    async def _director(objective: str) -> str:
        """
        Convert a human objective into an Aurora mission plan.

        Args:
            objective: The high-level objective supplied by the user.

        Returns:
            A structured initial mission plan.
        """
        objective = objective.strip()

        if not objective:
            return (
                f"{config.prefix}\n"
                "MISSION_REJECTED\n"
                "Reason: empty objective."
            )

        return (
            f"{config.prefix}\n"
            "MISSION_ACCEPTED\n\n"
            f"Objective:\n{objective}\n\n"
            "Initial orchestration plan:\n"
            "1. Analyse objective and constraints.\n"
            "2. Decompose objective into executable tasks.\n"
            "3. Select the appropriate specialist agents.\n"
            "4. Execute delegated tasks.\n"
            "5. Independently verify results.\n"
            "6. Consolidate verified results.\n"
            "7. Report status and remaining work."
        )

    yield FunctionInfo.from_fn(
        _director,
        description=_director.__doc__,
    )
