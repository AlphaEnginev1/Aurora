"""Dashboard system-health aggregation and incident history."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

from .gateway import DashboardGateway


_SENSITIVE = re.compile(
    r"(?:authorization|password|passwd|secret|credential|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret|token)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\\bBearer\\s+\\S+", re.IGNORECASE)
_BASIC = re.compile(r"\\bBasic\\s+[A-Za-z0-9+/=]+", re.IGNORECASE)


def _safe_string(value: str) -> str:
    return _BASIC.sub("Basic [redacted]", _BEARER.sub("Bearer [redacted]", value))


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[redacted]" if _SENSITIVE.search(str(k)) else _safe(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return _safe_string(value)
    return value


class DashboardHealthService:
    """Build a truthful health surface from Aurora's authoritative runtime data."""

    def __init__(self, gateway: DashboardGateway) -> None:
        self.gateway = gateway

    def health(self, *, incident_limit: int = 100) -> dict[str, Any]:
        if incident_limit < 1 or incident_limit > 500:
            raise ValueError("incident_limit must be between 1 and 500")

        snapshot = self.gateway.snapshot(event_limit=max(100, incident_limit))
        components: list[dict[str, Any]] = []

        director_ready = snapshot.get("director", {}).get("readiness") == "READY"
        components.append(self._component(
            "director", "Director", "CORE",
            "HEALTHY" if director_ready else "CRITICAL",
            "Director context is loaded and ready."
            if director_ready else "Director context is not ready.",
        ))

        runtime = snapshot.get("runtime", {})
        stale = self._stale_task_ids()
        failed_tasks = int(runtime.get("task_status_counts", {}).get("failed", 0))
        running = int(runtime.get("task_status_counts", {}).get("running", 0))
        worker_status = "DEGRADED" if stale else ("HEALTHY" if runtime.get("latest_events") else "UNKNOWN")
        worker_reason = (
            f"{len(stale)} stale running task(s)."
            if stale
            else ("Runtime is producing task events." if runtime.get("latest_events")
                  else "No runtime task evidence is currently available.")
        )
        components.append(self._component("worker", "Task Worker", "RUNTIME", worker_status, worker_reason))

        agent_statuses = []
        for agent in snapshot.get("organization", {}).get("agents", []):
            role = agent.get("role") or agent.get("agent_id")
            events = self.gateway.mailbox(agent_role=role, limit=25).get("events", [])
            failed = [e for e in events if e.get("event_type") in {"failed", "retry_scheduled"}]
            running_events = [e for e in events if e.get("event_type") == "claimed"]
            if failed:
                status = "DEGRADED"
                reason = f"{len(failed)} recent failure/retry signal(s)."
            elif events:
                status = "HEALTHY"
                reason = "Recent runtime activity is observable."
            else:
                status = "STANDBY"
                reason = "Registered agent with no recent execution evidence."
            agent_statuses.append({
                "agent_id": agent.get("agent_id"),
                "role": role,
                "department": agent.get("department"),
                "status": status,
                "reason": reason,
                "recent_events": len(events),
                "recent_failures": len(failed),
                "running_signals": len(running_events),
            })

        degraded_agents = sum(item["status"] == "DEGRADED" for item in agent_statuses)
        components.append(self._component(
            "agents", "Agents", "ORCHESTRATION",
            "DEGRADED" if degraded_agents else ("HEALTHY" if agent_statuses else "UNKNOWN"),
            f"{len(agent_statuses) - degraded_agents}/{len(agent_statuses)} agents have no recent failure signal."
            if agent_statuses else "No agents are registered.",
            details={"agents": agent_statuses},
        ))

        models = snapshot.get("models", [])
        healthy_models = [
            model for model in models
            if model.get("enabled") and model.get("healthy") and model.get("available")
        ]
        model_status = "HEALTHY" if healthy_models else ("DEGRADED" if models else "UNKNOWN")
        components.append(self._component(
            "models", "Model Layer", "INFERENCE", model_status,
            f"{len(healthy_models)} enabled, healthy and available model(s)."
            if healthy_models else "No enabled healthy and available model is currently registered.",
        ))

        providers = snapshot.get("providers", [])
        components.append(self._component(
            "providers", "Providers", "INFERENCE",
            "HEALTHY" if providers else "UNKNOWN",
            f"{len(providers)} provider definition(s) registered."
            if providers else "Provider registry is empty.",
        ))

        research = snapshot.get("research", {})
        research_state = research.get("state")
        components.append(self._component(
            "research", "Research", "KNOWLEDGE",
            "HEALTHY" if research_state not in {None, "unknown"} else "UNKNOWN",
            "Research store is configured and readable."
            if research_state not in {None, "unknown"}
            else "Research store is not configured or not observable.",
        ))

        observability = self.gateway.observability
        obs_ok = bool(observability and Path(observability.path).exists())
        components.append(self._component(
            "observability", "Observability", "OPERATIONS",
            "HEALTHY" if obs_ok else "UNKNOWN",
            "Durable observability stream is available."
            if obs_ok else "Durable observability stream is not currently observable.",
        ))

        components.append(self._component(
            "event_bus", "Event Bus", "RUNTIME", "UNKNOWN",
            "Event-bus process health is runtime-local and is not directly exposed by the read-only dashboard gateway.",
        ))
        components.append(self._component(
            "egress", "Egress Proxy", "SECURITY", "UNKNOWN",
            "OS/network enforcement is not asserted by the dashboard read model.",
        ))

        incidents = self._incidents(incident_limit)
        critical = any(item["status"] == "CRITICAL" for item in components)
        degraded = any(item["status"] == "DEGRADED" for item in components)
        unknown = any(item["status"] == "UNKNOWN" for item in components)
        if critical:
            overall = "CRITICAL"
        elif degraded:
            overall = "DEGRADED"
        elif unknown:
            overall = "UNKNOWN"
        else:
            overall = "HEALTHY"

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall": overall,
            "summary": {
                "components": len(components),
                "healthy": sum(item["status"] == "HEALTHY" for item in components),
                "degraded": sum(item["status"] == "DEGRADED" for item in components),
                "critical": sum(item["status"] == "CRITICAL" for item in components),
                "unknown": sum(item["status"] == "UNKNOWN" for item in components),
                "standby": sum(item["status"] == "STANDBY" for item in components),
                "active_incidents": sum(1 for item in incidents if item["status"] in {"OPEN", "DEGRADED"}),
                "failed_tasks": failed_tasks,
                "running_tasks": running,
                "stale_tasks": len(stale),
            },
            "components": components,
            "agents": agent_statuses,
            "incidents": incidents,
            "monitoring": {
                "mode": "read_only_runtime_observation",
                "truth_rule": "UNKNOWN is used whenever a critical signal cannot be directly observed.",
                "automatic_recovery": "Recovery remains governed by Aurora runtime policies; this screen does not mutate runtime state.",
                "incident_detail": "Each incident includes component, task/agent context, timestamps, error signals and available evidence.",
            },
        }

    def _component(
        self,
        key: str,
        name: str,
        layer: str,
        status: str,
        reason: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "name": name,
            "layer": layer,
            "status": status,
            "reason": reason,
            "details": _safe(details or {}),
        }

    def _stale_task_ids(self) -> list[str]:
        recovery: list[str] = []
        for mission_id in self.gateway.task_store.list_missions():
            tasks = self.gateway.task_store.list_tasks(mission_id)
            for task in tasks:
                if task.status == "running" and task.lease_until:
                    try:
                        lease = datetime.fromisoformat(task.lease_until)
                    except ValueError:
                        recovery.append(task.task.task_id)
                        continue
                    if lease <= datetime.now(timezone.utc):
                        recovery.append(task.task.task_id)
        return sorted(set(recovery))

    def _incidents(self, limit: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event in self.gateway._latest_events(limit):
            if event.event_type not in {"failed", "retry_scheduled", "blocked"}:
                continue
            payload = _safe(dict(event.payload))
            error = payload.get("error") or payload.get("error_code") or payload.get("reason")
            rows.append({
                "incident_id": f"task-event-{event.event_id}",
                "source": "task_store",
                "status": "OPEN" if event.event_type in {"failed", "blocked"} else "DEGRADED",
                "event_type": event.event_type,
                "created_at": event.created_at,
                "mission_id": event.mission_id,
                "task_id": event.task_id,
                "agent_role": event.agent_role,
                "project_id": event.project_id,
                "correlation_id": event.correlation_id,
                "error": str(error) if error is not None else "runtime failure signal without explicit error text",
                "evidence": payload,
            })

        observer = self.gateway.observability
        path = Path(observer.path) if observer is not None else None
        if path and path.exists():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if item.get("event_type") not in {"execution_failed", "task_requeued"}:
                            continue
                        rows.append({
                            "incident_id": f"observability-{item.get('event_id')}",
                            "source": "observability",
                            "status": "OPEN" if item.get("event_type") == "execution_failed" else "DEGRADED",
                            "event_type": item.get("event_type"),
                            "created_at": item.get("timestamp"),
                            "mission_id": item.get("mission_id"),
                            "task_id": item.get("task_id"),
                            "agent_role": item.get("agent_id") or item.get("role"),
                            "project_id": item.get("metadata", {}).get("project_id"),
                            "correlation_id": item.get("correlation_id"),
                            "error": item.get("error_code") or "execution failure signal",
                            "evidence": _safe(item),
                        })
            except OSError:
                pass

        rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        seen: set[str] = set()
        result = []
        for row in rows:
            if row["incident_id"] in seen:
                continue
            seen.add(row["incident_id"])
            result.append(row)
            if len(result) >= limit:
                break
        return result


__all__ = ["DashboardHealthService"]
