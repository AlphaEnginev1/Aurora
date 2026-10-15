"""Bind Aurora's autonomous controllers to the canonical runtime services.

This module is intentionally an adapter layer: it reuses the existing
DirectorService, ModelRouter, UniversalWorkbench, ResearchService and learning
store. It creates no parallel scheduler, router, research index or workbench.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from aurora.autonomy.computer_use import AutonomousComputerUse
from aurora.autonomy.discovery_loop import DiscoveryLoop
from aurora.autonomy.rsi import RecursiveSelfImprovement
from aurora.autonomy.software_engineer import AutonomousSoftwareEngineer
from aurora.inference.models import InferenceRequest
from aurora.research.models import EvidenceState, ResearchFinding, ResearchRequest
from aurora.research.knowledge import KnowledgeError, TrustedKnowledgeBuilder
from aurora.research.learning import ResearchLearningError, ResearchLearningGate
from aurora.research.verification import EvidenceRelation, VerificationError
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
        str(getattr(item, "status", getattr(item, "state", "unknown"))).strip().lower().rsplit(".", 1)[-1]
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

    candidate_claims: dict[str, Any] = {}

    def hypothesize(previous: str, evidence: Mapping[str, Any]) -> str:
        sources = [
            item for item in evidence.get("sources", ())
            if isinstance(item, Mapping)
            and str(item.get("source_id", "")).strip()
            and str(item.get("url", "")).strip()
        ]
        unique_urls = {str(item["url"]).strip() for item in sources}
        # A candidate source plus two separately retrieved sources are required
        # before the runtime even attempts to verify a claim.
        if len(unique_urls) < 3:
            return previous.strip()

        store = research_service.store
        candidates = []
        for item in sources:
            source = store.source(str(item["source_id"]))
            if source is None:
                continue
            try:
                extracted = research_service.extract_claims(source)
            except (ValueError, TypeError):
                continue
            independent_urls = {
                str(other["url"]).strip()
                for other in sources
                if str(other.get("source_id", "")) != source.source_id
                and str(other.get("url", "")).strip()
                and str(other.get("url", "")).strip() != source.url
            }
            if len(independent_urls) < 2:
                continue
            candidates.extend(extracted)

        # Bound model context and keep only claim text that was actually
        # extracted from a persisted source snapshot.
        unique_candidates = []
        seen_claim_ids = set()
        for claim in candidates:
            if claim.claim_id in seen_claim_ids:
                continue
            seen_claim_ids.add(claim.claim_id)
            unique_candidates.append(claim)
            if len(unique_candidates) >= 20:
                break
        if not unique_candidates:
            return previous.strip()

        payload_candidates = [
            {"claim_id": item.claim_id, "source_id": item.source_id, "claim": item.claim}
            for item in unique_candidates
        ]
        parsed = _json_object(_text(
            router,
            "Choose one candidate claim by its exact claim_id for independent verification. "
            "Return JSON only: {claim_id: string}. Select a literal claim extracted from one "
            "provided persisted source, not a paraphrase. Do not invent a claim.\n"
            + json.dumps(
                {"previous": previous, "candidates": payload_candidates},
                ensure_ascii=False,
                default=str,
            )[:10000],
        ))
        selected_id = str(parsed.get("claim_id", "")).strip()
        selected = next(
            (item for item in unique_candidates if item.claim_id == selected_id),
            None,
        )
        if selected is None:
            return previous.strip()
        candidate_claims[selected.claim] = selected
        return selected.claim

    def falsify(hypothesis: str, evidence: Mapping[str, Any]) -> Mapping[str, Any]:
        claim = candidate_claims.get(hypothesis)
        sources = [
            item for item in evidence.get("sources", ())
            if isinstance(item, Mapping)
            and str(item.get("source_id", "")).strip()
            and str(item.get("url", "")).strip()
        ]
        source_ids = {str(item["source_id"]) for item in sources}
        if claim is None or claim.source_id not in source_ids:
            return {"verdict": "insufficient", "evidence": []}

        candidate_source = research_service.store.source(claim.source_id)
        if candidate_source is None:
            return {"verdict": "insufficient", "evidence": []}
        independent_urls = tuple(dict.fromkeys(
            str(item["url"]).strip()
            for item in sources
            if str(item["source_id"]) != claim.source_id
            and str(item["url"]).strip()
            and str(item["url"]).strip() != candidate_source.url
        ))
        if len(independent_urls) < 2:
            return {"verdict": "insufficient", "evidence": []}

        def classify_relation(candidate, source, parsed_text: str) -> EvidenceRelation:
            parsed = _json_object(_text(
                router,
                "Classify whether this independent source provides support, contradiction, or no "
                "clear evidence for the exact candidate claim. Return JSON only: "
                "{relation: support|contradict|unclear, evidence_text: verbatim passage}. "
                "evidence_text must be copied exactly from the supplied source; if no exact "
                "passage supports the relation, use relation unclear.\n"
                + json.dumps(
                    {
                        "claim": candidate.claim,
                        "source_id": source.source_id,
                        "source_text": parsed_text[:7000],
                    },
                    ensure_ascii=False,
                    default=str,
                )[:10000],
            ))
            relation = str(parsed.get("relation", "unclear")).strip().lower()
            if relation not in {"support", "contradict", "unclear"}:
                relation = "unclear"
            quoted_text = str(parsed.get("evidence_text", "")).strip()
            normalize = lambda value: re.sub(r"\s+", " ", value).strip().casefold()
            if not quoted_text or normalize(quoted_text) not in normalize(parsed_text):
                # Model prose is not evidence unless the cited passage exists
                # verbatim in the independently retrieved source.
                relation = "unclear"
                quoted_text = "No matching verbatim passage was provided."
            return EvidenceRelation(
                source_id=source.source_id,
                relation=relation,
                evidence_text=quoted_text,
            )

        try:
            assessment = research_service.verify_claim(
                claim,
                independent_urls,
                classify_relation,
            )
        except VerificationError:
            return {"verdict": "insufficient", "evidence": []}

        assessment_state = str(getattr(assessment.state, "value", assessment.state)).lower()
        if assessment_state == EvidenceState.CONTRADICTED.value:
            return {
                "verdict": "falsified",
                "evidence": [],
                "source_ids": list(assessment.contradicting_source_ids),
                "rationale": assessment.rationale,
            }
        if assessment_state != EvidenceState.VERIFIED.value:
            return {
                "verdict": "insufficient",
                "evidence": [],
                "source_ids": list(assessment.source_ids),
                "rationale": assessment.rationale,
            }

        verified_evidence = []
        for source_id in assessment.supporting_source_ids:
            verified_evidence.extend(
                item for item in research_service.store.evidence_for_source(source_id)
                if item.claim == claim.claim and item.state is EvidenceState.VERIFIED
            )
        evidence_ids = tuple(dict.fromkeys(item.evidence_id for item in verified_evidence))
        if len(evidence_ids) < 2:
            return {"verdict": "insufficient", "evidence": [], "rationale": "verified evidence records were not persisted"}
        return {
            "verdict": "verified",
            "evidence": list(evidence_ids),
            "source_ids": list(assessment.supporting_source_ids),
            "rationale": assessment.rationale,
        }

    def novelty(hypothesis: str, assessment: Mapping[str, Any]) -> str:
        answer = _text(router,
            "Classify whether the claim is novel or already known based only on supplied evidence. "
            "Return exactly 'novel', 'known', or 'uncertain'.\n"
            + json.dumps({"hypothesis": hypothesis, "assessment": assessment}, ensure_ascii=False, default=str)[:8000]
        ).strip().lower()
        return answer if answer in {"novel", "known", "uncertain"} else "uncertain"

    def learn(hypothesis: str, assessment: Mapping[str, Any]) -> str | None:
        if str(assessment.get("verdict", "")).strip().lower() != "verified":
            return None
        claim = candidate_claims.get(hypothesis)
        if claim is None:
            return None

        requested_ids = {
            str(item).strip()
            for item in assessment.get("evidence", ())
            if str(item).strip()
        }
        evidence = []
        source_ids = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in assessment.get("source_ids", ())
                if str(item).strip()
            )
        )
        for source_id in source_ids:
            evidence.extend(
                item for item in research_service.store.evidence_for_source(source_id)
                if item.evidence_id in requested_ids
                and item.claim == claim.claim
                and item.state is EvidenceState.VERIFIED
            )
        evidence = tuple({item.evidence_id: item for item in evidence}.values())
        if len(evidence) < 2 or {item.evidence_id for item in evidence} != requested_ids:
            return None

        finding_id = "finding-" + hashlib.sha256(
            f"{claim.claim_id}|{claim.claim}|{'|'.join(sorted(requested_ids))}".encode("utf-8")
        ).hexdigest()[:32]
        finding = ResearchFinding(
            finding_id=finding_id,
            claim=claim.claim,
            evidence_ids=tuple(item.evidence_id for item in evidence),
            confidence=1.0,
            state=EvidenceState.VERIFIED,
        )
        try:
            research_service.store.record_finding(finding)
            artifact, _ = TrustedKnowledgeBuilder(research_service.store).build(
                finding,
                evidence,
            )
            # This is the same trust gate used before research-derived results
            # can enter adaptive learning. Only its durable artifact ID leaves
            # this callback; source IDs alone never masquerade as knowledge.
            ResearchLearningGate(research_service.store).authorize((artifact.artifact_id,))
        except (KnowledgeError, ResearchLearningError, ValueError):
            return None
        return artifact.artifact_id

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
