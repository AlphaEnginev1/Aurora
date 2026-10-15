"""Director startup bootstrap from authoritative Aurora state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from aurora.context import ContextGate, ContextManifest, ContextScope
from aurora.orchestration.director_decision_knowledge_store import (
    DirectorDecisionKnowledgeStore,
)
from aurora.orchestration.director_decision_performance import (
    DirectorDecisionPerformanceStore,
)
from aurora.orchestration.director_decision_policy_active_store import (
    DirectorDecisionPolicyActiveStore,
)
from aurora.orchestration.director_decision_policy_contract_store import (
    DirectorDecisionPolicyContractStore,
)
from aurora.orchestration.director_decision_policy_proposal_store import (
    DirectorDecisionPolicyProposalStore,
)


class DirectorBootstrapError(RuntimeError):
    """Raised when the Director cannot start from authoritative state."""


@dataclass(frozen=True)
class DirectorBootstrapState:
    """Immutable startup snapshot used before the first Director conversation."""

    boot_id: str
    initialized_at: str
    context: ContextManifest
    director_model_policy: Mapping[str, Any]
    active_decision_policy: Mapping[str, Any] | None
    active_decision_contract: Mapping[str, Any] | None
    decision_performance: tuple[Mapping[str, Any], ...]
    decision_knowledge: tuple[Mapping[str, Any], ...]

    @property
    def messages(self) -> list[dict[str, str]]:
        messages = list(self.context.messages)
        messages.append(
            {
                "role": "system",
                "content": "[aurora-context source=director.model_policy "
                "provenance=inference-policy]\n"
                + json.dumps(self.director_model_policy, sort_keys=True),
            }
        )
        if self.active_decision_policy is not None:
            messages.append(
                {
                    "role": "system",
                    "content": "[aurora-context source=director.active_policy "
                    "provenance=policy-store]\n"
                    + json.dumps(self.active_decision_policy, sort_keys=True),
                }
            )
        if self.active_decision_contract is not None:
            messages.append(
                {
                    "role": "system",
                    "content": "[aurora-context source=director.active_policy_contract "
                    "provenance=policy-contract-store]\n"
                    + json.dumps(self.active_decision_contract, sort_keys=True),
                }
            )
        if self.decision_performance:
            messages.append(
                {
                    "role": "system",
                    "content": "[aurora-context source=director.decision_performance "
                    "provenance=decision-performance-store]\n"
                    + json.dumps(list(self.decision_performance), sort_keys=True),
                }
            )
        if self.decision_knowledge:
            messages.append(
                {
                    "role": "system",
                    "content": "[aurora-context source=director.decision_knowledge "
                    "provenance=decision-knowledge-store]\n"
                    + json.dumps(list(self.decision_knowledge), sort_keys=True),
                }
            )
        return messages


@dataclass(frozen=True)
class DirectorBootstrap:
    """Load the authoritative Director startup contract exactly once."""

    context_gate: ContextGate
    model_policy_path: str | Path
    decision_performance_store: DirectorDecisionPerformanceStore
    active_policy_store: DirectorDecisionPolicyActiveStore
    policy_proposal_store: DirectorDecisionPolicyProposalStore
    policy_contract_store: DirectorDecisionPolicyContractStore
    decision_knowledge_store: DirectorDecisionKnowledgeStore

    def initialize(self) -> DirectorBootstrapState:
        policy = self._load_model_policy()
        context = self.context_gate.prepare(
            scope=ContextScope.DIRECTOR,
            require_mailbox=True,
            require_knowledge=True,
        )
        active_policy = self.active_policy_store.current()
        active_policy_payload = None
        active_contract_payload = None
        if active_policy is not None:
            active_policy_payload = asdict(active_policy)
            proposal = self.policy_proposal_store.get(active_policy.proposal_id)
            if proposal is not None:
                active_policy_payload["proposal"] = asdict(proposal)
            for contract in self.policy_contract_store.list_all():
                if (
                    contract.proposal_id == active_policy.proposal_id
                    and contract.validation_id == active_policy.validation_id
                ):
                    active_contract_payload = asdict(contract)
                    break

        performance = tuple(
            asdict(item)
            for item in self.decision_performance_store.summary(minimum_evaluations=1)
        )
        knowledge = tuple(
            asdict(item) for item in self.decision_knowledge_store.list_all()
        )
        return DirectorBootstrapState(
            boot_id=f"boot-{uuid4().hex}",
            initialized_at=datetime.now(timezone.utc).isoformat(),
            context=context,
            director_model_policy=policy,
            active_decision_policy=active_policy_payload,
            active_decision_contract=active_contract_payload,
            decision_performance=performance,
            decision_knowledge=knowledge,
        )

    def _load_model_policy(self) -> dict[str, Any]:
        path = Path(self.model_policy_path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DirectorBootstrapError(
                f"unable to load Director model policy: {path}"
            ) from exc
        if not isinstance(raw, dict):
            raise DirectorBootstrapError("Director model policy must be an object")

        default_capability = raw.get("default_capability")
        providers = raw.get("providers")
        models = raw.get("models")
        routing = raw.get("routing")
        if not isinstance(default_capability, str) or not default_capability.strip():
            raise DirectorBootstrapError("Director model policy requires default_capability")
        if not isinstance(providers, dict):
            raise DirectorBootstrapError("Director model policy requires providers")
        if not isinstance(models, list):
            raise DirectorBootstrapError("Director model policy requires models")
        if not isinstance(routing, dict):
            raise DirectorBootstrapError("Director model policy requires routing")

        sanitized_providers: dict[str, dict[str, Any]] = {}
        for provider_id, provider in providers.items():
            if not isinstance(provider_id, str) or not provider_id.strip():
                raise DirectorBootstrapError("Director model policy contains an invalid provider id")
            if not isinstance(provider, dict):
                raise DirectorBootstrapError(
                    f"Director model policy provider {provider_id!r} must be an object"
                )
            sanitized_providers[provider_id] = {
                "enabled": bool(provider.get("enabled", True)),
                "api_key_env": provider.get("api_key_env"),
                "base_url": provider.get("base_url"),
            }

        sanitized_models: list[dict[str, Any]] = []
        for item in models:
            if not isinstance(item, dict):
                raise DirectorBootstrapError("Director model policy model entries must be objects")
            model_id = item.get("id")
            provider = item.get("provider")
            if not isinstance(model_id, str) or not model_id.strip():
                raise DirectorBootstrapError("Director model policy model requires id")
            if not isinstance(provider, str) or not provider.strip():
                raise DirectorBootstrapError(
                    f"Director model policy model {model_id!r} requires provider"
                )
            sanitized_models.append(
                {
                    "id": model_id,
                    "provider": provider,
                    "enabled": bool(item.get("enabled", True)),
                    "capabilities": list(item.get("capabilities", ())),
                    "context_window": item.get("context_window"),
                }
            )

        return {
            "version": raw.get("version"),
            "default_capability": default_capability,
            "providers": sanitized_providers,
            "models": sanitized_models,
            "routing": dict(routing),
        }


def build_context_runtime_adapter(
    task_store,
) -> Any:
    """Create a compact authoritative runtime-state adapter for the Context Plane."""

    def runtime_context(_project_id: str | None) -> dict[str, object]:
        missions = task_store.list_missions()
        states: dict[str, int] = {
            "pending": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "blocked": 0,
        }
        for mission_id in missions:
            status = task_store.mission_status(mission_id)
            states[status] = states.get(status, 0) + 1
        return {
            "mission_count": len(missions),
            "mission_status_counts": states,
        }

    return runtime_context


def build_context_mailbox_adapter(task_store, *, limit: int = 40) -> Any:
    """Expose shared events globally and strictly project-tagged events per project.

    An event with no project_id is ambiguous: task-store-generated events use
    NULL too, so it must not be treated as public/global inside a project
    context. Project-bound model requests therefore see only events explicitly
    tagged to that project. The Director/global context (project_id=None) keeps
    the cross-project overview.
    """

    if limit < 1:
        raise DirectorBootstrapError("mailbox limit must be at least 1")

    def mailbox_context(project_id: str | None) -> tuple[dict[str, object], ...]:
        try:
            events = task_store.latest_events(limit=limit, project_id=project_id)
        except Exception as exc:
            raise DirectorBootstrapError(
                f"unable to load shared mailbox index: {exc}"
            ) from exc
        return tuple(
            {
                "event_id": int(event.event_id),
                "mission_id": str(event.mission_id),
                "task_id": event.task_id,
                "event_type": str(event.event_type),
                "payload": dict(event.payload),
                "created_at": str(event.created_at),
                "project_id": event.project_id,
                "agent_role": event.agent_role,
                "correlation_id": event.correlation_id,
            }
            for event in events
        ) or ({"state": "empty", "event_count": 0, "project_id": project_id},)

    return mailbox_context


def build_context_knowledge_adapter(research_store, *, limit: int = 50) -> Any:
    """Expose the research knowledge/evidence index without injecting full documents."""

    if limit < 1:
        raise DirectorBootstrapError("knowledge limit must be at least 1")

    def knowledge_context(_project_id: str | None) -> tuple[dict[str, object], ...]:
        try:
            with research_store._connect() as db:
                rows = db.execute(
                    """
                    SELECT artifact_id, finding_id, claim, evidence_ids,
                           source_ids, state, created_at
                    FROM knowledge_artifacts
                    ORDER BY created_at DESC, artifact_id
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except Exception as exc:
            raise DirectorBootstrapError(
                f"unable to load knowledge/evidence index: {exc}"
            ) from exc
        return tuple(
            {
                "artifact_id": str(row["artifact_id"]),
                "finding_id": str(row["finding_id"]),
                "claim": str(row["claim"]),
                "evidence_count": len(json.loads(row["evidence_ids"] or "[]")),
                "source_count": len(json.loads(row["source_ids"] or "[]")),
                "state": str(row["state"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ) or ({"state": "empty", "artifact_count": 0},)

    return knowledge_context


__all__ = [
    "DirectorBootstrap",
    "DirectorBootstrapError",
    "DirectorBootstrapState",
    "build_context_knowledge_adapter",
    "build_context_mailbox_adapter",
    "build_context_runtime_adapter",
]
