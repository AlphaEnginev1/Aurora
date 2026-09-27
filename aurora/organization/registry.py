"""Declarative organization registry for Aurora agents and departments."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


class OrganizationConfigError(ValueError):
    """Raised when the Aurora organization registry is invalid."""


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    department: str
    mission: str
    capabilities: frozenset[str]
    model_preferences: tuple[str, ...]
    skill_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DepartmentSpec:
    department_id: str
    mission: str
    agent_ids: tuple[str, ...]
    model_preferences: tuple[str, ...]
    allowed_capabilities: frozenset[str]


class OrganizationRegistry:
    """Load and resolve Aurora's departments, agents and routing policy."""

    def __init__(
        self,
        *,
        agents: dict[str, AgentSpec],
        departments: dict[str, DepartmentSpec],
    ) -> None:
        self.agents = dict(agents)
        self.departments = dict(departments)
        self._roles = {agent.role: agent for agent in agents.values()}
        self._validate()

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        configured_models: set[str] | frozenset[str] | None = None,
    ) -> "OrganizationRegistry":
        source = Path(path)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OrganizationConfigError(
                f"Unable to load organization registry: {source}"
            ) from exc

        if not isinstance(data, dict):
            raise OrganizationConfigError("organization registry must be an object")

        raw_departments = data.get("departments")
        raw_agents = data.get("agents")
        if not isinstance(raw_departments, list) or not raw_departments:
            raise OrganizationConfigError("'departments' must be a non-empty list")
        if not isinstance(raw_agents, list) or not raw_agents:
            raise OrganizationConfigError("'agents' must be a non-empty list")

        agents: dict[str, AgentSpec] = {}
        for item in raw_agents:
            if not isinstance(item, dict):
                raise OrganizationConfigError("each agent must be an object")
            required = ("agent_id", "role", "department", "mission")
            if not all(isinstance(item.get(key), str) and item[key].strip() for key in required):
                raise OrganizationConfigError(
                    "agent requires non-empty agent_id, role, department and mission"
                )
            capabilities = item.get("capabilities", [])
            model_preferences = item.get("model_preferences", [])
            skill_ids = item.get("skill_ids", [])
            if (
                not isinstance(capabilities, list)
                or not all(isinstance(value, str) and value.strip() for value in capabilities)
            ):
                raise OrganizationConfigError(
                    f"invalid capabilities for agent {item.get('agent_id')!r}"
                )
            if (
                not isinstance(model_preferences, list)
                or not all(
                    isinstance(value, str) and value.strip()
                    for value in model_preferences
                )
            ):
                raise OrganizationConfigError(
                    f"invalid model_preferences for agent {item.get('agent_id')!r}"
                )
            if (
                not isinstance(skill_ids, list)
                or not all(isinstance(value, str) and value.strip() for value in skill_ids)
            ):
                raise OrganizationConfigError(
                    f"invalid skill_ids for agent {item.get('agent_id')!r}"
                )
            agent = AgentSpec(
                agent_id=item["agent_id"],
                role=item["role"],
                department=item["department"],
                mission=item["mission"],
                capabilities=frozenset(capabilities),
                model_preferences=tuple(model_preferences),
                skill_ids=tuple(skill_ids),
            )
            if agent.agent_id in agents:
                raise OrganizationConfigError(
                    f"duplicate agent_id: {agent.agent_id}"
                )
            if agent.role in {existing.role for existing in agents.values()}:
                raise OrganizationConfigError(f"duplicate role: {agent.role}")
            agents[agent.agent_id] = agent

        departments: dict[str, DepartmentSpec] = {}
        for item in raw_departments:
            if not isinstance(item, dict):
                raise OrganizationConfigError("each department must be an object")
            required = ("department_id", "mission")
            if not all(isinstance(item.get(key), str) and item[key].strip() for key in required):
                raise OrganizationConfigError(
                    "department requires non-empty department_id and mission"
                )
            agent_ids = item.get("agent_ids", [])
            model_preferences = item.get("model_preferences", [])
            allowed_capabilities = item.get("allowed_capabilities", [])
            if not isinstance(agent_ids, list) or not all(
                isinstance(value, str) and value.strip() for value in agent_ids
            ):
                raise OrganizationConfigError(
                    f"invalid agent_ids for department {item.get('department_id')!r}"
                )
            if not isinstance(model_preferences, list) or not all(
                isinstance(value, str) and value.strip()
                for value in model_preferences
            ):
                raise OrganizationConfigError(
                    f"invalid model_preferences for department {item.get('department_id')!r}"
                )
            if not isinstance(allowed_capabilities, list) or not all(
                isinstance(value, str) and value.strip()
                for value in allowed_capabilities
            ):
                raise OrganizationConfigError(
                    f"invalid allowed_capabilities for department {item.get('department_id')!r}"
                )
            department = DepartmentSpec(
                department_id=item["department_id"],
                mission=item["mission"],
                agent_ids=tuple(agent_ids),
                model_preferences=tuple(model_preferences),
                allowed_capabilities=frozenset(allowed_capabilities),
            )
            if department.department_id in departments:
                raise OrganizationConfigError(
                    f"duplicate department_id: {department.department_id}"
                )
            departments[department.department_id] = department

        registry = cls(agents=agents, departments=departments)
        if configured_models is not None:
            registry.validate_model_preferences(set(configured_models))
        return registry

    def _validate(self) -> None:
        for department in self.departments.values():
            for agent_id in department.agent_ids:
                agent = self.agents.get(agent_id)
                if agent is None:
                    raise OrganizationConfigError(
                        f"department {department.department_id} references unknown agent {agent_id}"
                    )
                if agent.department != department.department_id:
                    raise OrganizationConfigError(
                        f"agent {agent_id} belongs to {agent.department}, "
                        f"not {department.department_id}"
                    )
                if not agent.capabilities.issubset(department.allowed_capabilities):
                    missing = sorted(agent.capabilities - department.allowed_capabilities)
                    raise OrganizationConfigError(
                        f"agent {agent_id} requests undeclared capabilities: {', '.join(missing)}"
                    )

        for agent in self.agents.values():
            if agent.department not in self.departments:
                raise OrganizationConfigError(
                    f"agent {agent.agent_id} references unknown department {agent.department}"
                )

    def validate_model_preferences(self, configured_models: set[str]) -> None:
        for model_id in {
            *(
                model
                for department in self.departments.values()
                for model in department.model_preferences
            ),
            *(
                model
                for agent in self.agents.values()
                for model in agent.model_preferences
            ),
        }:
            if model_id not in configured_models:
                raise OrganizationConfigError(
                    f"organization references model not present in inference policy: {model_id}"
                )

    def agents_by_role(self) -> dict[str, AgentSpec]:
        """Return the registered agent roles as an immutable-by-copy mapping."""
        return {agent.role: agent for agent in self.agents.values()}

    def resolve_agent(self, role_or_agent_id: str) -> AgentSpec:
        key = role_or_agent_id.strip()
        if not key:
            raise OrganizationConfigError("agent role is empty")
        agent = self.agents.get(key) or self._roles.get(key)
        if agent is None:
            raise OrganizationConfigError(
                f"unknown Aurora agent role: {role_or_agent_id}"
            )
        return agent

    def department_for(self, role_or_agent_id: str) -> DepartmentSpec:
        agent = self.resolve_agent(role_or_agent_id)
        return self.departments[agent.department]

    def model_preferences_for(self, role_or_agent_id: str) -> tuple[str, ...]:
        agent = self.resolve_agent(role_or_agent_id)
        department = self.departments[agent.department]
        ordered = []
        for model_id in (*agent.model_preferences, *department.model_preferences):
            if model_id not in ordered:
                ordered.append(model_id)
        return tuple(ordered)

    def capabilities_for(self, role_or_agent_id: str) -> frozenset[str]:
        return self.resolve_agent(role_or_agent_id).capabilities

    def validate_task(
        self,
        *,
        role: str,
        capability: str | None,
    ) -> AgentSpec:
        agent = self.resolve_agent(role)
        if capability is not None and capability not in agent.capabilities:
            raise OrganizationConfigError(
                f"agent {role} is not authorized for capability {capability}"
            )
        return agent


__all__ = [
    "AgentSpec",
    "DepartmentSpec",
    "OrganizationConfigError",
    "OrganizationRegistry",
]
