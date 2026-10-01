"""Authoritative, read-only Dashboard Gateway and snapshot builder."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from typing import Any, Mapping
from uuid import uuid4

from aurora.context import ContextManifest
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.inference.providers.registry import ProviderRegistry
from aurora.inference.registry import ModelRegistry
from aurora.observability import ProductionObservability
from aurora.organization import OrganizationRegistry
from aurora.projects import ProjectRegistry
from aurora.research.store import ResearchStore
from aurora.skills import SkillLearningStore, SkillRegistry
from aurora.orchestration.director_decision_knowledge_store import (
    DirectorDecisionKnowledgeStore,
)
from aurora.orchestration.director_decision_performance import (
    DirectorDecisionPerformanceStore,
)
from aurora.orchestration.director_decision_policy_active_store import (
    DirectorDecisionPolicyActiveStore,
)
from aurora.orchestration.mission_memory import MailboxScope, MissionMemory
from aurora.orchestration.task_store import DurableTaskStore, StoredTask, TaskEvent


class DashboardGatewayError(RuntimeError):
    """Raised when an authoritative dashboard read cannot be completed safely."""


class DashboardGateway:
    """Read-only aggregation boundary for the Aurora Dashboard."""

    def __init__(
        self,
        *,
        project_registry: ProjectRegistry,
        organization: OrganizationRegistry,
        skill_registry: SkillRegistry,
        model_registry: ModelRegistry,
        provider_registry: ProviderRegistry,
        task_store: DurableTaskStore,
        mission_memory: MissionMemory,
        model_performance: ModelPerformanceStore | None = None,
        skill_learning: SkillLearningStore | None = None,
        research_store: ResearchStore | None = None,
        decision_performance: DirectorDecisionPerformanceStore | None = None,
        decision_knowledge: DirectorDecisionKnowledgeStore | None = None,
        active_policy_store: DirectorDecisionPolicyActiveStore | None = None,
        observability: ProductionObservability | None = None,
        director_context: ContextManifest | None = None,
        director_model_policy: Mapping[str, Any] | None = None,
    ) -> None:
        self.project_registry = project_registry
        self.organization = organization
        self.skill_registry = skill_registry
        self.model_registry = model_registry
        self.provider_registry = provider_registry
        self.task_store = task_store
        self.mission_memory = mission_memory
        self.model_performance = model_performance
        self.skill_learning = skill_learning
        self.research_store = research_store
        self.decision_performance = decision_performance
        self.decision_knowledge = decision_knowledge
        self.active_policy_store = active_policy_store
        self.observability = observability
        self.director_context = director_context
        self.director_model_policy = (
            dict(director_model_policy) if director_model_policy is not None else None
        )

    def snapshot(self, *, mailbox_limit: int = 50, event_limit: int = 100) -> dict[str, Any]:
        self._validate_limit(mailbox_limit, "mailbox_limit")
        self._validate_limit(event_limit, "event_limit")
        generated_at = _now()

        missions = self.task_store.list_missions()
        task_rows: list[StoredTask] = []
        for mission_id in missions:
            task_rows.extend(self.task_store.list_tasks(mission_id))

        tasks_by_status: dict[str, int] = {}
        for task in task_rows:
            tasks_by_status[task.status] = tasks_by_status.get(task.status, 0) + 1

        projects = tuple(self.project_registry.projects.values())
        latest_events = self._latest_events(event_limit)
        mailbox = self.mission_memory.list_global(limit=mailbox_limit)

        snapshot = {
            "snapshot_id": f"dash-{uuid4().hex}",
            "generated_at": generated_at,
            "read_only": True,
            "sources": self._sources(generated_at),
            "director": self._director_view(),
            "projects": [self._project_summary(project) for project in projects],
            "organization": {
                "departments": [
                    {
                        "department_id": department_id,
                        "mission": department.mission,
                        "agent_ids": list(department.agent_ids),
                        "model_preferences": list(department.model_preferences),
                        "allowed_capabilities": sorted(department.allowed_capabilities),
                    }
                    for department_id, department in sorted(
                        self.organization.departments.items()
                    )
                ],
                "agents": [self._agent_summary(agent) for agent in self.organization.agents.values()],
            },
            "providers": list(self.provider_registry.catalog),
            "models": list(self.model_registry.catalog),
            "skills": [self._skill_summary(skill) for skill in self.skill_registry.skills.values()],
            "runtime": {
                "mission_count": len(missions),
                "task_count": len(task_rows),
                "task_status_counts": dict(sorted(tasks_by_status.items())),
                "latest_events": [self._event(event) for event in latest_events],
                "workers": {"state": "unknown", "reason": "worker topology is runtime-local"},
            },
            "mailbox": {
                "messages": [self._message(message) for message in mailbox],
                "scope": MailboxScope.GLOBAL.value,
            },
            "model_intelligence": self.model_intelligence(limit=50),
            "skills_learning": self.skills_learning(limit=50),
            "research": self.research(limit=50),
            "governance": self.governance(limit=50),
        }
        return snapshot

    def project(self, project_id: str, *, limit: int = 50) -> dict[str, Any]:
        self._validate_limit(limit, "limit")
        project = self.project_registry.resolve(project_id)
        tasks = self._tasks_for_project(project.project_id, project.task_ids)
        events = self._latest_events(limit, project_id=project.project_id)
        messages = self.mission_memory.list_project(project.project_id, limit=limit)
        agents = [
            self._agent_detail(role)
            for role in project.agent_roles
            if self.organization.resolve_agent(role)
        ]
        return {
            "project": self._project_detail(project),
            "agents": agents,
            "tasks": [self._task(task) for task in tasks],
            "events": [self._event(event) for event in events],
            "mailbox": [self._message(message) for message in messages],
            "models": [self._model_by_id(model_id) for model_id in project.model_ids],
            "skills": [self._skill_by_id(skill_id) for skill_id in project.skill_ids],
            "decisions": list(project.decision_ids),
            "knowledge": self._research_project_view(project.project_id, limit=limit),
            "recovery": self._project_recovery(tasks),
            "freshness": {
                "captured_at": _now(),
                "event_history_is_filtered_by_project_id": True,
                "research_scope": "global",
            },
        }

    def agent(self, role_or_agent_id: str, *, limit: int = 50) -> dict[str, Any]:
        self._validate_limit(limit, "limit")
        agent = self.organization.resolve_agent(role_or_agent_id)
        tasks = self._tasks_for_role(agent.role)
        projects = [
            project
            for project in self.project_registry.projects.values()
            if agent.role in project.agent_roles
        ]
        events = self._latest_events(limit, agent_role=agent.role)
        messages = self.mission_memory.list_agent(agent.role, limit=limit)

        return {
            "identity": self._agent_detail(agent.role),
            "projects": [self._project_summary(project) for project in projects],
            "current_tasks": [self._task(task) for task in tasks if task.status in {"running", "pending"}],
            "recent_executions": [self._task(task) for task in tasks[:limit]],
            "events": [self._event(event) for event in events],
            "mailbox": [self._message(message) for message in messages],
            "skills": [self._skill_by_id(skill_id) for skill_id in agent.skill_ids],
            "model_preferences": [self._model_by_id(model_id) for model_id in agent.model_preferences],
            "performance": self._agent_performance(agent.role),
            "capabilities": sorted(agent.capabilities),
            "freshness": {"captured_at": _now()},
        }

    def mailbox(
        self,
        *,
        project_id: str | None = None,
        agent_role: str | None = None,
        mission_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._validate_limit(limit, "limit")
        events = self._latest_events(
            limit,
            project_id=project_id,
            agent_role=agent_role,
            mission_id=mission_id,
            correlation_id=correlation_id,
        )
        if project_id is not None:
            messages = self.mission_memory.list_project(project_id, limit=limit)
        elif agent_role is not None:
            messages = self.mission_memory.list_agent(agent_role, limit=limit)
        elif correlation_id is not None:
            messages = self.mission_memory.list_correlation(correlation_id, limit=limit)
        elif mission_id is not None:
            messages = self.mission_memory.list(mission_id, limit=limit)
        else:
            messages = self.mission_memory.list_global(limit=limit)
        return {
            "filters": {
                "project_id": project_id,
                "agent_role": agent_role,
                "mission_id": mission_id,
                "correlation_id": correlation_id,
            },
            "events": [self._event(event) for event in events],
            "messages": [self._message(message) for message in messages],
            "freshness": {"captured_at": _now()},
        }

    def model_intelligence(self, *, model_id: str | None = None, limit: int = 50) -> dict[str, Any]:
        self._validate_limit(limit, "limit")
        models = self.model_registry.models
        if model_id is not None:
            models = (self.model_registry.resolve(model_id),)
        result = {
            "providers": list(self.provider_registry.catalog),
            "models": [
                self._model(model) for model in models
            ],
            "performance": [],
        }
        if self.model_performance is not None:
            result["performance"] = self._recent_model_performance(limit)
        return result

    def skills_learning(self, *, skill_id: str | None = None, limit: int = 50) -> dict[str, Any]:
        self._validate_limit(limit, "limit")
        skills = (
            (self.skill_registry.resolve(skill_id),)
            if skill_id is not None
            else tuple(self.skill_registry.skills.values())
        )
        result = {"skills": [], "evolution_proposals": []}
        for skill in skills:
            item = self._skill_summary(skill)
            if self.skill_learning is not None:
                item["performance"] = self._jsonable(self.skill_learning.performance(skill.skill_id))
                item["evidence"] = [
                    self._jsonable(row)
                    for row in self.skill_learning.evidence_for(
                        skill.skill_id, skill_version=skill.version, limit=limit
                    )
                ]
            result["skills"].append(item)
        if self.skill_learning is not None:
            proposals = self.skill_learning.pending_proposals(skill_id)
            result["evolution_proposals"] = [
                self._jsonable(row) for row in proposals[:limit]
            ]
        return result

    def research(self, *, limit: int = 50) -> dict[str, Any]:
        self._validate_limit(limit, "limit")
        if self.research_store is None:
            return {"state": "unknown", "reason": "research store not configured"}
        return self._research_view(limit)

    def governance(self, *, limit: int = 50) -> dict[str, Any]:
        self._validate_limit(limit, "limit")
        result: dict[str, Any] = {
            "decision_performance": [],
            "decision_knowledge": [],
            "active_policy": None,
            "context_manifest_id": (
                self.director_context.manifest_id
                if self.director_context is not None
                else None
            ),
        }
        if self.decision_performance is not None:
            result["decision_performance"] = [
                self._jsonable(item)
                for item in self.decision_performance.summary(minimum_evaluations=1)
            ][:limit]
        if self.decision_knowledge is not None:
            result["decision_knowledge"] = [
                self._jsonable(item)
                for item in self.decision_knowledge.list_all()
            ][:limit]
        result["active_policy"] = self._active_policy_view()
        return result

    def _director_view(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "identity": "Aurora Director",
            "readiness": "READY" if self.director_context is not None else "UNKNOWN",
            "model_policy": self.director_model_policy,
            "context_manifest_id": (
                self.director_context.manifest_id
                if self.director_context is not None
                else None
            ),
            "context_scope": (
                self.director_context.scope.value
                if self.director_context is not None
                else None
            ),
            "context_unknown_fields": (
                list(self.director_context.unknown_fields)
                if self.director_context is not None
                else ["director_bootstrap"]
            ),
            "current_model": None,
        "configured_models": [
            item for item in (self.director_model_policy or {}).get("models", [])
            if isinstance(item, Mapping)
        ],
        }
        return result

    def _director_model(self) -> dict[str, Any] | None:
        policy = self.director_model_policy or {}
        models = policy.get("models")
        if not isinstance(models, list) or not models:
            return None
        selected = next(
            (
                item
                for item in models
                if isinstance(item, Mapping) and item.get("enabled", True)
            ),
            None,
        )
        if not isinstance(selected, Mapping):
            return None
        model_id = selected.get("id")
        if isinstance(model_id, str):
            try:
                return self._model_by_id(model_id)
            except Exception:
                return {
                    "model_id": model_id,
                    "provider": selected.get("provider"),
                    "state": "configured_only",
                }
        return None

    def _active_policy_view(self) -> dict[str, Any] | None:
        store = self.active_policy_store
        if store is None:
            return None
        current = store.current()
        return self._jsonable(current) if current is not None else None

    def _agent_summary(self, agent: Any) -> dict[str, Any]:
        return {
            "agent_id": agent.agent_id,
            "role": agent.role,
            "department": agent.department,
            "mission": agent.mission,
            "capabilities": sorted(agent.capabilities),
            "model_preferences": list(agent.model_preferences),
            "skill_ids": list(agent.skill_ids),
        }

    def _agent_detail(self, role: str) -> dict[str, Any]:
        agent = self.organization.resolve_agent(role)
        department = self.organization.department_for(role)
        return {
            **self._agent_summary(agent),
            "department_mission": department.mission,
            "department_allowed_capabilities": sorted(department.allowed_capabilities),
            "authorized_capabilities": sorted(agent.capabilities),
        }

    def _project_summary(self, project: Any) -> dict[str, Any]:
        return {
            "project_id": project.project_id,
            "name": project.name,
            "objective": project.objective,
            "status": project.status.value,
            "repository": project.repository,
            "workspace": project.workspace,
            "architecture_ref": project.architecture_ref,
            "agent_roles": list(project.agent_roles),
            "skill_ids": list(project.skill_ids),
            "model_ids": list(project.model_ids),
            "current_state": project.current_state or "unknown",
            "updated_at": project.updated_at or None,
        }

    def _project_detail(self, project: Any) -> dict[str, Any]:
        return {
            **self._project_summary(project),
            "task_ids": list(project.task_ids),
            "decision_ids": list(project.decision_ids),
            "created_at": project.created_at or None,
        }

    def _model(self, model: Any) -> dict[str, Any]:
        return {
            "model_id": model.id,
            "provider": model.provider,
            "endpoint": model.endpoint,
            "enabled": model.enabled,
            "healthy": model.healthy,
            "available": model.available,
            "capabilities": list(model.capabilities),
            "specialties": list(model.specialties),
            "context_window": model.context_window,
            "quality_score": model.quality_score,
            "latency_score": model.latency_score,
            "cost_score": model.cost_score,
        }

    def _model_by_id(self, model_id: str) -> dict[str, Any]:
        return self._model(self.model_registry.resolve(model_id))

    def _skill_summary(self, skill: Any) -> dict[str, Any]:
        return {
            "skill_id": skill.skill_id,
            "version": skill.version,
            "name": skill.name,
            "mission": skill.mission,
            "enabled": skill.enabled,
            "required_capabilities": sorted(skill.required_capabilities),
            "model_preferences": list(skill.model_preferences),
            "dependencies": list(skill.dependencies),
            "verification_requirements": list(skill.verification_requirements),
            "quality_checks": list(skill.quality_checks),
        }

    def _skill_by_id(self, skill_id: str) -> dict[str, Any]:
        return self._skill_summary(self.skill_registry.resolve(skill_id))

    def _tasks_for_role(self, role: str) -> list[StoredTask]:
        results: list[StoredTask] = []
        for mission_id in self.task_store.list_missions():
            results.extend(
                item for item in self.task_store.list_tasks(mission_id)
                if item.task.role == role
            )
        return sorted(results, key=lambda item: (item.task.task_id, item.mission_id))

    def _tasks_for_project(self, project_id: str, task_ids: tuple[str, ...]) -> list[StoredTask]:
        wanted = set(task_ids)
        if not wanted:
            return []
        results: list[StoredTask] = []
        for mission_id in self.task_store.list_missions():
            results.extend(
                item
                for item in self.task_store.list_tasks(mission_id)
                if item.task.task_id in wanted
            )
        return sorted(
            results,
            key=lambda item: (item.task.task_id, item.mission_id),
        )

    def _latest_events(
        self,
        limit: int,
        *,
        mission_id: str | None = None,
        project_id: str | None = None,
        agent_role: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TaskEvent, ...]:
        return self.task_store.latest_events(
            limit=limit,
            mission_id=mission_id,
            project_id=project_id,
            agent_role=agent_role,
            correlation_id=correlation_id,
        )

    def _event(self, event: TaskEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "mission_id": event.mission_id,
            "task_id": event.task_id,
            "event_type": event.event_type,
            "payload": dict(event.payload),
            "created_at": event.created_at,
            "project_id": event.project_id,
            "agent_role": event.agent_role,
            "correlation_id": event.correlation_id,
        }

    def _message(self, message: Any) -> dict[str, Any]:
        return {
            "message_id": message.message_id,
            "mission_id": message.mission_id,
            "source": message.source,
            "kind": message.kind,
            "content": message.content,
            "metadata": dict(message.metadata),
            "created_at": message.created_at,
            "scope": message.scope.value,
            "project_id": message.project_id,
            "agent_role": message.agent_role,
            "task_id": message.task_id,
            "event_id": message.event_id,
            "correlation_id": message.correlation_id,
        }

    def _project_recovery(self, tasks: list[StoredTask]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        stale = []
        for task in tasks:
            if task.status != "running" or not task.lease_until:
                continue
            try:
                if datetime.fromisoformat(task.lease_until) <= now:
                    stale.append(task.task.task_id)
            except ValueError:
                stale.append(task.task.task_id)
        return {
            "stale_task_ids": stale,
            "state": "attention_required" if stale else "clear",
        }

    def _agent_performance(self, role: str) -> dict[str, Any]:
        if self.skill_learning is None:
            return {"state": "unknown"}
        scores = self.skill_learning.agent_scores_for(
            tuple(self.skill_registry.ids()),
            minimum_evaluations=1,
            minimum_evidence_completeness=0.0,
        )
        return {
            "agent_role": role,
            "score": scores.get(role),
            "known": role in scores,
        }

    def _recent_model_performance(self, limit: int) -> list[dict[str, Any]]:
        store = self.model_performance
        if store is None:
            return []
        with store._connect() as db:
            rows = db.execute(
                """
                SELECT model, skill_id, skill_version, task_type, context_bucket,
                       score, evidence_completeness, evidence_state,
                       created_at, routing_score, routing_factors, routing_candidates
                FROM model_performance
                ORDER BY performance_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "model": row["model"],
                "skill_id": row["skill_id"],
                "skill_version": row["skill_version"],
                "task_type": row["task_type"],
                "context_bucket": row["context_bucket"],
                "score": float(row["score"]),
                "evidence_completeness": float(row["evidence_completeness"]),
                "evidence_state": row["evidence_state"],
                "created_at": row["created_at"],
                "routing_score": row["routing_score"],
                "routing_factors": json.loads(row["routing_factors"] or "{}"),
                "routing_candidates": json.loads(row["routing_candidates"] or "[]"),
            }
            for row in rows
        ]

    def _research_view(self, limit: int) -> dict[str, Any]:
        store = self.research_store
        if store is None:
            return {"state": "unknown"}
        with store._connect() as db:
            counts = {
                "sources": int(db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]),
                "claims": int(db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]),
                "evidence": int(db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]),
                "findings": int(db.execute("SELECT COUNT(*) FROM findings").fetchone()[0]),
                "knowledge_artifacts": int(
                    db.execute("SELECT COUNT(*) FROM knowledge_artifacts").fetchone()[0]
                ),
                "citation_edges": int(
                    db.execute("SELECT COUNT(*) FROM citation_edges").fetchone()[0]
                ),
            }
            artifacts = db.execute(
                """
                SELECT artifact_id, finding_id, claim, evidence_ids, source_ids,
                       state, created_at
                FROM knowledge_artifacts
                ORDER BY created_at DESC, artifact_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {
            "counts": counts,
            "recent_knowledge": [
                {
                    "artifact_id": row["artifact_id"],
                    "finding_id": row["finding_id"],
                    "claim": row["claim"],
                    "evidence_count": len(json.loads(row["evidence_ids"] or "[]")),
                    "source_count": len(json.loads(row["source_ids"] or "[]")),
                    "state": row["state"],
                    "created_at": row["created_at"],
                }
                for row in artifacts
            ],
        }

    def _research_project_view(self, project_id: str, *, limit: int) -> dict[str, Any]:
        if self.research_store is None:
            return {"state": "unknown"}
        return {
            "project_id": project_id,
            "state": "available",
            "recent_knowledge": self.research(limit=limit),
            "scope": "global",
        }

    def _active_policy_store_bind(self, store: Any) -> None:
        self._active_policy_store = store

    def _sources(self, captured_at: str) -> dict[str, Any]:
        return {
            "projects": {"state": "authoritative", "captured_at": captured_at},
            "organization": {"state": "authoritative", "captured_at": captured_at},
            "skills": {"state": "authoritative", "captured_at": captured_at},
            "models": {"state": "authoritative", "captured_at": captured_at},
            "providers": {"state": "authoritative", "captured_at": captured_at},
            "tasks_events": {"state": "authoritative", "captured_at": captured_at},
            "mailbox": {"state": "authoritative_projection", "captured_at": captured_at},
            "research": {
                "state": "authoritative" if self.research_store is not None else "unknown",
                "captured_at": captured_at,
            },
            "observability": {
                "state": "non_authoritative"
                if self.observability is not None
                else "unknown",
                "captured_at": captured_at,
            },
        }

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if is_dataclass(value):
            return DashboardGateway._jsonable(asdict(value))
        if isinstance(value, Mapping):
            return {str(key): DashboardGateway._jsonable(item) for key, item in value.items()}
        if isinstance(value, (tuple, list, set, frozenset)):
            return [DashboardGateway._jsonable(item) for item in value]
        if hasattr(value, "value") and not isinstance(value, (str, bytes)):
            return value.value
        return value

    @staticmethod
    def _validate_limit(limit: int, name: str) -> None:
        if limit < 1 or limit > 1000:
            raise DashboardGatewayError(f"{name} must be between 1 and 1000")
