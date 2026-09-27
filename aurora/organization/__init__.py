"""Aurora organizational contracts."""

from .registry import (
    AgentSpec,
    DepartmentSpec,
    OrganizationConfigError,
    OrganizationRegistry,
)
from .agent_selection import AgentSelection, AgentSelectionError, AgentSelector
from .agent_team import AgentTeam, AgentTeamError, DynamicAgentTeamSelector, TeamMember, TeamReconfiguration, reconfigure_team

__all__ = [
    "AgentSpec",
    "DepartmentSpec",
    "OrganizationConfigError",
    "OrganizationRegistry",
    "AgentSelection",
    "AgentSelectionError",
    "AgentSelector",
    "AgentTeam",
    "AgentTeamError",
    "DynamicAgentTeamSelector",
    "TeamMember",
    "TeamReconfiguration",
    "reconfigure_team",
]
