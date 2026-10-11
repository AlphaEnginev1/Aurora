"""Bind Aurora's autonomous controllers to the canonical runtime services.

This module is intentionally an adapter layer: it reuses the existing
DirectorService, ModelRouter, UniversalWorkbench, ResearchService and learning
store. It creates no parallel scheduler, router, research index or workbench.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from uuid import uuid4

from aurora.autonomy.computer_use import AutonomousComputerUse
from aurora.autonomy.discovery_loop import DiscoveryLoop
from aurora.autonomy.rsi import RecursiveSelfImprovement
from aurora.autonomy.software_engineer import AutonomousSoftwareEngineer
from aurora.inference.models import InferenceRequest
from aurora.research.models import ResearchRequest
from aurora.workbench import WorkbenchRequest


def _mission_id(prefix: str, objective: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{objective}".encode("utf-8")).hexdigest()[:20]
    return f"autonomy-{prefix}-{digest}"


def _text(router, prompt: str) -> str:
    response = router.generate(
        InferenceRequest(
            messages=[
                {"role": "system", "content": "Follow the requested format exactly. Do not claim external actions or verification that are not in the supplied evidence."},
                {"role": "user", "content": prompt},
            ],
            metadata={"task_type": "autonomy_controller", "source": "canonical_runtime"},
        ),
        capability="reasoning",
    )
    return str(response.content).strip()


def _json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        if value.endswith("```"):
            value = value[:-3].strip()
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _mission_summary(director_service, phase: str, objective: str, state: Mapping[str, Any]) -> dict[str, Any]:
    context = [{
        "role": "user",
        "content": json.dumps(
            {"controller": phase, "objective": objective, "state": dict(state)},
            ensure_ascii=False,
            default=str,
        )[:12000],
    }]
    result = director_service.run_mission(
        objective=f"Autonomous engineering controller phase '{phase}': {objective}",
        context=context,
    )
    tasks = tuple(getattr(result, "tasks", ()) or ())
    statuses = [
        str(getattr(item, "status", getattr(item, "state", "unknown"))).strip().lower()
        for item in tasks
    ]
    success_states = {"success", "succeeded", "complete", "completed"}
    failed_states = {"failed", "error", "blocked", "cancelled", "canceled"}
    succeeded = bool(statuses) and all(item in success_states for item in statuses)
    failed = any(item in failed_states for item in statuses)
    return {
        "phase": phase,
        "objective": objective,
        "task_count": len(tasks),
        "task_statuses": statuses,
        "phase_succeeded": succeeded,
        "phase_failed": failed,
        "mission_objective": str(getattr(result, "objective", objective)),
    }


def build_autonomy_runtime_bindings(*, director_service, router, workbench, research_service, skill_learning):
    """Return the four remaining controllers wired to canonical live services.

    Controllers are constructed only; no work is launched during runtime startup.
    Each controller remains explicitly invocable and subject to Workbench policy.
    """
    def computer_observe() -> Mapping[str, Any]:
        result = workbench.execute(WorkbenchRequest(
            capability="workbench:census",
            actor="aurora-autonomous-computer-use",
            mission_id="autonomy-computer-census",
            reason="Observe available governed workstation capabilities",
        ))
        if result.status != "succeeded":
            return {"status": result.status, "error": result.error}
        return {"capabilities": result.output}

    def computer_execute(actions: list[dict[str, Any]]) -> Mapping[str, Any]:
        objective = str(actions[0].get("objective", "computer action")) if actions else "computer action"
        result = workbench.execute(WorkbenchRequest(
            capability="workbench:computer",
            actor="aurora-autonomous-computer-use",
            mission_id=_mission_id("computer", objective),
            reason="Execute one bounded action through the governed local computer gateway",
            arguments=(json.dumps(actions, ensure_ascii=False),),
        ))
        if result.status != "succeeded":
            return {"completed": False, "status": result.status, "error": result.error}
        output = result.output
        return dict(output) if isinstance(output, Mapping) else {"completed": False, "output": output}

    def computer_decide(context: Mapping[str, Any]) -> Mapping[str, Any] | None:
        prompt = (
            "Choose at most one safe next action for Aurora's governed computer gateway. "
            "Return JSON object only, or null when complete. Never request secrets, "
            "credential extraction, destructive changes, or actions outside the stated objective.\n"
            + json.dumps(context, ensure_ascii=False, default=str)[:10000]
        )
        parsed = _json_object(_text(router, prompt))
        action = parsed.get("action")
        if action is None or parsed.get("done") is True:
            return None
        return action if isinstance(action, dict) else None

    computer_use = AutonomousComputerUse(
        execute=computer_execute,
        observe=computer_observe,
        decide=computer_decide,
        max_steps=16,
    )

    def engineering_execute(phase: str, state: Mapping[str, Any]) -> Mapping[str, Any]:
        objective = str(state.get("objective", "Complete the requested software engineering task"))
        return _mission_summary(director_service, phase, objective, state)

    def engineering_evaluate(state: Mapping[str, Any]) -> str:
        if state.get("phase_failed"):
            return "blocked"
        if state.get("phase") == "publish":
            return "succeeded" if state.get("phase_succeeded") else "blocked"
        return "continue"

    software_engineer = AutonomousSoftwareEngineer(
        execute=engineering_execute,
        evaluate=engineering_evaluate,
        max_iterations=7,
    )

    def investigate(hypothesis: str) -> Mapping[str, Any]:
        request = ResearchRequest(
            request_id=f"autonomy-discovery-{uuid4().hex}",
            objective=hypothesis,
            max_sources=5,
        )
        urls = tuple(research_service.discover(request))
        if not urls:
            return {"urls": [], "sources": [], "source_ids": []}
        sources = research_service.retrieve(ResearchRequest(
            request_id=request.request_id + "-retrieve",
            objective=hypothesis,
            required_sources=urls[:5],
            max_sources=min(5, len(urls)),
        ))
        return {
            "urls": [source.url for source in sources],
            "sources": [
                {"url": source.url, "source_id": source.source_id, "text": source.content_text[:5000]}
                for source in sources
            ],
            "source_ids": [source.source_id for source in sources],
        }

    def hypothesize(previous: str, evidence: Mapping[str, Any]) -> str:
        return _text(router, "State one testable hypothesis based only on this research evidence.\n"
                     + json.dumps({"previous": previous, "evidence": evidence}, ensure_ascii=False, default=str)[:10000])

    def falsify(hypothesis: str, evidence: Mapping[str, Any]) -> Mapping[str, Any]:
        sources = evidence.get("sources", [])
        unique_urls = {str(item.get("url", "")) for item in sources if isinstance(item, Mapping)}
        if len(unique_urls) < 2:
            return {"verdict": "insufficient", "evidence": list(evidence.get("source_ids", []))}
        parsed = _json_object(_text(router,
            "Evaluate whether the hypothesis is supported, falsified, or still insufficient. "
            "Use only supplied evidence. Return JSON {verdict: supported|falsified|insufficient, evidence: [source ids]}. "
            "Do not claim formal verification.\n"
            + json.dumps({"hypothesis": hypothesis, "evidence": sources}, ensure_ascii=False, default=str)[:12000]
        ))
        verdict = str(parsed.get("verdict", "insufficient")).lower()
        if verdict not in {"supported", "falsified", "insufficient"}:
            verdict = "insufficient"
        ids = [str(item) for item in parsed.get("evidence", []) if str(item)]
        return {"verdict": verdict, "evidence": ids}

    def novelty(hypothesis: str, assessment: Mapping[str, Any]) -> str:
        answer = _text(router,
            "Classify whether the claim is novel or already known based only on supplied evidence. "
            "Return exactly 'novel', 'known', or 'uncertain'.\n"
            + json.dumps({"hypothesis": hypothesis, "assessment": assessment}, ensure_ascii=False, default=str)[:8000]
        ).strip().lower()
        return answer if answer in {"novel", "known", "uncertain"} else "uncertain"

    def learn(hypothesis: str, assessment: Mapping[str, Any]) -> str | None:
        # ResearchService.retrieve has already durably persisted source artifacts.
        # Return their stable IDs as the evidence artifact; never promote an
        # unsupported claim directly into trusted knowledge or skill policy.
        ids = tuple(str(item) for item in assessment.get("evidence", ()) if str(item))
        return "research-evidence:" + ",".join(ids) if ids else None

    discovery_loop = DiscoveryLoop(
        investigate=investigate,
        hypothesize=hypothesize,
        falsify=falsify,
        novelty=novelty,
        learn=learn,
        max_cycles=4,
    )

    def rsi_execute(phase: str, state: Mapping[str, Any]) -> Mapping[str, Any]:
        objective = str(state.get("objective", "Improve Aurora safely"))
        result = _mission_summary(director_service, phase, objective, state)
        # A CI result must be explicit evidence; a Director mission finishing is
        # not equivalent to GitHub CI passing. The controller therefore fails
        # closed unless its existing service path returns both fields.
        return result

    def rsi_evaluate(state: Mapping[str, Any]) -> str:
        if state.get("phase_failed"):
            return "blocked"
        if state.get("ci_status") == "success" and state.get("change_id"):
            return "improved"
        return "insufficient"

    rsi = RecursiveSelfImprovement(
        execute_phase=rsi_execute,
        evaluate=rsi_evaluate,
        max_cycles=1,
    )

    return {
        "computer_use": computer_use,
        "software_engineer": software_engineer,
        "discovery_loop": discovery_loop,
        "recursive_self_improvement": rsi,
        "learning_store": skill_learning,
    }
