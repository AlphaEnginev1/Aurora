"""Bind Aurora's autonomous controllers to the canonical runtime services.

This module is intentionally an adapter layer: it reuses the existing
DirectorService, ModelRouter, UniversalWorkbench, ResearchService and learning
store. It creates no parallel scheduler, router, research index or workbench.
"""
from __future__ import annotations

import hashlib
import json
import os
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
    """Run one Director phase and preserve verifiable GitHub change/CI receipts.

    A successful Director mission is not itself proof that a file was committed
    or that CI passed. Those facts are derived only from successful canonical
    Workbench GitHub capability results.
    """
    phase_instructions = {
        "inspect": "Inspect the target and report concrete repository evidence. Do not edit or publish files.",
        "diagnose": "Identify a specific, bounded root cause and minimal repair. Do not edit or publish files.",
        "research": "Ground the repair in persisted evidence and identify the exact files and tests needed.",
        "modify": (
            "Apply only the minimal repair to a dedicated non-default GitHub branch using "
            "workbench:github:branch and workbench:github:file:write. Do not write to main/master "
            "or merge. Return the branch, repository, and resulting commit receipt."
        ),
        "edit": (
            "Apply only the minimal repair to a dedicated non-default GitHub branch using "
            "workbench:github:branch and workbench:github:file:write. Do not write to main/master "
            "or merge. Return the branch, repository, and resulting commit receipt."
        ),
        "plan": "Produce a bounded implementation plan with validation criteria; do not claim changes were made.",
        "test": (
            "Validate the exact candidate change. A successful Director task alone is not proof of CI; "
            "the later CI phase must read GitHub Actions for the exact commit SHA."
        ),
        "ci": (
            "Use workbench:github:workflow:read to inspect the GitHub Actions workflow named CI. "
            "Only a completed success run whose head_sha exactly matches the candidate commit proves CI."
        ),
        "verify": (
            "Use workbench:github:workflow:read to inspect the GitHub Actions workflow named CI. "
            "Only a completed success run whose head_sha exactly matches the candidate commit proves verification."
        ),
        "publish": (
            "Open a pull request for the verified feature branch using workbench:github:pr only after the exact "
            "commit has successful CI. The PR must remain open and target the configured base branch; never merge "
            "automatically. Do not claim publication without a successful matching GitHub PR receipt."
        ),
        "learn": (
            "Summarize reusable lessons only from the exact change whose GitHub CI completed successfully. "
            "Do not claim the change succeeded without the commit and CI receipts in the supplied state."
        ),
    }
    phase_instruction = phase_instructions.get(
        phase,
        "Carry out only the stated phase. Do not claim external actions without execution receipts.",
    )
    context = [{
        "role": "user",
        "content": json.dumps(
            {
                "controller": phase,
                "objective": objective,
                "phase_instruction": phase_instruction,
                "state": dict(state),
            },
            ensure_ascii=False,
            default=str,
        )[:12000],
    }]
    result = director_service.run_mission(
        objective=(
            f"Autonomous engineering controller phase '{phase}': {objective}. "
            f"Required contract: {phase_instruction}"
        ),
        context=context,
    )
    tasks = tuple(getattr(result, "tasks", ()) or ())
    statuses = [
        str(getattr(item, "status", getattr(item, "state", "unknown")))
        .strip().lower().rsplit(".", 1)[-1]
        for item in tasks
    ]
    success_states = {"success", "succeeded", "complete", "completed"}
    failed_states = {"failed", "error", "blocked", "cancelled", "canceled"}
    succeeded = bool(statuses) and all(item in success_states for item in statuses)
    failed = any(item in failed_states for item in statuses)
    summary: dict[str, Any] = {
        "phase": phase,
        "objective": objective,
        "task_count": len(tasks),
        "task_statuses": statuses,
        "phase_succeeded": succeeded,
        "phase_failed": failed,
        "mission_objective": str(getattr(result, "objective", objective)),
    }

    def execution_receipt(item):
        task = getattr(item, "task", None)
        capability = str(getattr(task, "capability", "") or "")
        capability_result = getattr(item, "capability_result", None)
        execution = getattr(capability_result, "execution", capability_result)
        status = str(getattr(execution, "status", "")).strip().lower().rsplit(".", 1)[-1]
        output = getattr(execution, "output", None)
        provenance = getattr(execution, "provenance", None)
        if status not in success_states or provenance != "github-api" or not isinstance(output, Mapping):
            return None
        return task, capability, output

    if phase in {"modify", "edit"}:
        writes = []
        saw_write = False
        rejected_write = False
        configured_base = os.getenv("AURORA_RSI_BASE_BRANCH", "main").strip().lower() or "main"
        protected_branches = {configured_base, "main", "master"}

        for item in tasks:
            receipt = execution_receipt(item)
            task = getattr(item, "task", None)
            capability = str(getattr(task, "capability", "") or "")
            if capability != "workbench:github:file:write":
                continue
            saw_write = True
            receipt = execution_receipt(item)
            if receipt is None:
                rejected_write = True
                continue
            task, _, output = receipt
            raw_args = tuple(getattr(task, "args", ()) or ())
            request = {}
            for raw in raw_args:
                if not isinstance(raw, str):
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    request = parsed
                    break
            owner = str(request.get("owner", "")).strip()
            repository = str(request.get("repo", "")).strip()
            repository = f"{owner}/{repository}" if owner and repository else ""
            branch_name = str(request.get("branch", "")).strip()
            commit = output.get("commit")
            commit_sha = str(commit.get("sha", "")).strip() if isinstance(commit, Mapping) else ""
            if (
                not repository
                or not branch_name
                or branch_name.lower() in protected_branches
                or not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha)
            ):
                rejected_write = True
                continue
            raw_parents = commit.get("parents", ()) if isinstance(commit, Mapping) else ()
            parents = tuple(
                str(parent.get("sha", "")).strip()
                for parent in raw_parents
                if isinstance(parent, Mapping)
            )
            writes.append({
                "change_id": commit_sha,
                "change_repository": repository,
                "change_branch": branch_name,
                "commit_parents": parents,
            })

        if saw_write and not rejected_write and writes:
            identities = {
                (item["change_repository"], item["change_branch"])
                for item in writes
            }
            commit_ids = [item["change_id"] for item in writes]
            commit_map = {
                item["change_id"]: item["commit_parents"]
                for item in writes
            }
            valid_chain = len(identities) == 1 and len(set(commit_ids)) == len(commit_ids)

            if len(writes) > 1:
                # GitHub Contents writes each create a commit. Accept multi-file
                # edits only if the API receipts prove one linear commit chain.
                # Parallel/divergent writes have no unambiguous tested head and
                # must fail closed.
                valid_chain = valid_chain and all(
                    len(commit_map[sha]) == 1
                    and re.fullmatch(r"[0-9a-fA-F]{40}", commit_map[sha][0])
                    for sha in commit_map
                )
                parents = {
                    sha: values[0]
                    for sha, values in commit_map.items()
                    if len(values) == 1
                }
                internal_parents = {
                    parent for parent in parents.values() if parent in commit_map
                }
                tips = [sha for sha in commit_map if sha not in internal_parents]
                if len(tips) != 1:
                    valid_chain = False
                else:
                    visited = set()
                    current = tips[0]
                    while current in commit_map and current not in visited:
                        visited.add(current)
                        current = parents.get(current, "")
                    valid_chain = valid_chain and len(visited) == len(commit_map)

                if valid_chain:
                    tip_sha = tips[0]
                    tip = next(item for item in writes if item["change_id"] == tip_sha)
                    summary.update({
                        "change_id": tip["change_id"],
                        "change_repository": tip["change_repository"],
                        "change_branch": tip["change_branch"],
                        "change_write_receipts": len(writes),
                    })
                else:
                    summary["change_receipt_rejected"] = (
                        "multiple GitHub file-write receipts must form one linear commit chain "
                        "proved by commit parents on the same non-default branch and repository"
                    )
            elif valid_chain:
                summary.update({
                    key: value for key, value in writes[0].items()
                    if key != "commit_parents"
                })
                summary["change_write_receipts"] = 1
            else:
                summary["change_receipt_rejected"] = (
                    "GitHub file-write receipts target multiple repositories or branches"
                )
        elif saw_write:
            summary["change_receipt_rejected"] = (
                "GitHub file-write receipt missing, invalid, or targets a protected branch"
            )

    if phase in {"ci", "verify"}:
        expected_sha = str(state.get("change_id", "")).strip().lower()
        matched_runs = []
        relevant_runs = []
        if expected_sha:
            for item in tasks:
                receipt = execution_receipt(item)
                if receipt is None:
                    continue
                _, capability, output = receipt
                if capability != "workbench:github:workflow:read":
                    continue
                runs = output.get("workflow_runs", ())
                if not isinstance(runs, list):
                    continue
                for run in runs:
                    if not isinstance(run, Mapping):
                        continue
                    if str(run.get("name", "")).strip().casefold() != "ci":
                        continue
                    if str(run.get("head_sha", "")).strip().lower() != expected_sha:
                        continue
                    relevant_runs.append(run)
                    if (
                        str(run.get("status", "")).strip().lower() == "completed"
                        and str(run.get("conclusion", "")).strip().lower() == "success"
                    ):
                        matched_runs.append(run)
        # Treat a completed failed CI run for the same SHA as failure; a
        # similarly named run for another SHA cannot satisfy this contract.
        if matched_runs:
            selected_run = max(
                matched_runs,
                key=lambda item: int(item.get("run_number", 0) or 0),
            )
            summary.update({
                "ci_status": "success",
                "ci_run_id": selected_run.get("id"),
                "ci_head_sha": expected_sha,
                "ci_workflow_name": selected_run.get("name"),
            })
        elif any(
            str(item.get("status", "")).strip().lower() == "completed"
            and str(item.get("conclusion", "")).strip().lower() in {"failure", "cancelled", "timed_out", "action_required"}
            for item in relevant_runs
        ):
            summary.update({"ci_status": "failure", "ci_head_sha": expected_sha})
        else:
            summary.update({"ci_status": "insufficient", "ci_head_sha": expected_sha or None})

    if phase == "publish":
        saw_pr = False
        rejected_pr = False
        base_branch = os.getenv("AURORA_RSI_BASE_BRANCH", "main").strip() or "main"
        expected_repository = str(state.get("change_repository", "")).strip()
        expected_branch = str(state.get("change_branch", "")).strip()
        expected_sha = str(state.get("change_id", "")).strip().lower()
        for item in tasks:
            task = getattr(item, "task", None)
            capability = str(getattr(task, "capability", "") or "")
            if capability != "workbench:github:pr":
                continue
            saw_pr = True
            receipt = execution_receipt(item)
            if receipt is None:
                rejected_pr = True
                continue
            task, _, output = receipt
            raw_args = tuple(getattr(task, "args", ()) or ())
            request = {}
            for raw in raw_args:
                if not isinstance(raw, str):
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    request = parsed
                    break
            owner = str(request.get("owner", "")).strip()
            repository = str(request.get("repo", "")).strip()
            requested_repository = f"{owner}/{repository}" if owner and repository else ""
            requested_head = str(request.get("head", "")).strip()
            requested_base = str(request.get("base", "")).strip()
            pr_head = output.get("head")
            pr_base = output.get("base")
            pr_number = output.get("number")
            pr_url = str(output.get("html_url", "")).strip()
            valid = (
                expected_sha
                and expected_repository
                and expected_branch
                and requested_repository == expected_repository
                and requested_head in {expected_branch, f"{owner}:{expected_branch}"}
                and requested_base == base_branch
                and isinstance(pr_head, Mapping)
                and isinstance(pr_base, Mapping)
                and str(output.get("state", "")).strip().lower() == "open"
                and str(pr_head.get("ref", "")).strip() == expected_branch
                and str(pr_head.get("sha", "")).strip().lower() == expected_sha
                and str(pr_base.get("ref", "")).strip() == base_branch
                and pr_base.get("ref") != pr_head.get("ref")
                and isinstance(pr_number, int)
                and pr_number > 0
                and pr_url.startswith(f"https://github.com/{expected_repository}/pull/")
                and pr_url.rstrip("/").endswith(f"/{pr_number}")
            )
            if not valid:
                rejected_pr = True
                continue
            summary.update({
                "pull_request_id": pr_number,
                "pull_request_url": pr_url,
                "pull_request_state": "open",
                "pull_request_head_sha": expected_sha,
                "pull_request_head_branch": expected_branch,
                "pull_request_repository": expected_repository,
                "pull_request_base_branch": base_branch,
            })
            break
        if saw_pr and (rejected_pr or not summary.get("pull_request_id")):
            summary["pull_request_receipt_rejected"] = (
                "GitHub PR receipt is missing, mismatched, merged, or targets an invalid branch"
            )

    return summary


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
        mission_id = _mission_id("computer", objective)
        action_fingerprint = hashlib.sha256(
            json.dumps(actions, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
        result = workbench.execute(WorkbenchRequest(
            capability="workbench:computer",
            actor="aurora-autonomous-computer-use",
            mission_id=mission_id,
            reason="Execute one bounded action through the governed local computer gateway",
            arguments=(json.dumps(actions, ensure_ascii=False),),
            options={"work_id": mission_id},
            idempotency_key=(
                f"autonomy-computer:{mission_id}:{action_fingerprint}"
                if getattr(workbench, "session_store", None) is not None
                else None
            ),
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
        # Do not continue launching Director missions after a required evidence
        # contract fails in an earlier engineering phase.
        if state.get("phase_failed"):
            return {
                "phase": phase,
                "phase_succeeded": False,
                "phase_failed": True,
                "blocked_by": state.get("phase"),
                "error": state.get("error") or "an earlier engineering phase failed",
            }
        if phase in {"test", "verify", "publish"} and not state.get("change_id"):
            return {
                "phase": phase,
                "phase_succeeded": False,
                "phase_failed": True,
                "error": f"{phase} requires a verified GitHub commit receipt from the edit phase",
            }
        if phase == "publish" and (
            state.get("ci_status") != "success"
            or state.get("ci_head_sha") != state.get("change_id")
            or not state.get("ci_run_id")
        ):
            return {
                "phase": phase,
                "phase_succeeded": False,
                "phase_failed": True,
                "error": "publish requires completed successful CI for the exact candidate commit",
            }

        objective = str(state.get("objective", "Complete the requested software engineering task"))
        result = _mission_summary(director_service, phase, objective, state)
        if not result.get("phase_succeeded"):
            result["phase_failed"] = True
            result["error"] = "Director engineering phase returned no successful, complete task evidence"
            return result
        if phase == "edit" and not result.get("change_id"):
            result["phase_failed"] = True
            result["phase_succeeded"] = False
            result["error"] = (
                "edit did not return a successful GitHub file-write receipt on a non-default branch"
            )
            return result
        if phase == "verify" and (
            result.get("ci_status") != "success"
            or result.get("ci_head_sha") != state.get("change_id")
            or not result.get("ci_run_id")
        ):
            result["phase_failed"] = True
            result["phase_succeeded"] = False
            result["error"] = "verify requires completed successful GitHub CI for the exact commit SHA"
            return result
        if phase == "publish" and (
            result.get("pull_request_state") != "open"
            or result.get("pull_request_head_sha") != state.get("change_id")
            or result.get("pull_request_head_branch") != state.get("change_branch")
            or result.get("pull_request_repository") != state.get("change_repository")
        ):
            result["phase_failed"] = True
            result["phase_succeeded"] = False
            result["error"] = "publish did not return an open GitHub PR for the verified commit and branch"
            return result
        return result

    def engineering_evaluate(state: Mapping[str, Any]) -> str:
        if state.get("phase_failed"):
            return "blocked"
        if state.get("phase") == "publish":
            return (
                "succeeded"
                if state.get("phase_succeeded")
                and state.get("pull_request_state") == "open"
                and state.get("pull_request_head_sha") == state.get("change_id")
                and state.get("pull_request_head_branch") == state.get("change_branch")
                and state.get("pull_request_repository") == state.get("change_repository")
                else "blocked"
            )
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
        # Once a phase fails its evidence contract, do not launch later generic
        # missions; preserve the blocker and stop producing side effects.
        if state.get("phase_failed"):
            return {
                "phase": phase,
                "phase_succeeded": False,
                "phase_failed": True,
                "blocked_by": state.get("phase"),
                "error": state.get("error") or "a prior phase failed its evidence contract",
            }

        if phase in {"test", "ci", "learn"} and not state.get("change_id"):
            return {
                "phase": phase,
                "phase_succeeded": False,
                "phase_failed": True,
                "error": f"{phase} requires a verified GitHub commit receipt from the modify phase",
            }
        if phase == "learn" and (
            state.get("ci_status") != "success"
            or state.get("ci_head_sha") != state.get("change_id")
            or not state.get("ci_run_id")
        ):
            return {
                "phase": phase,
                "phase_succeeded": False,
                "phase_failed": True,
                "error": "learn requires a completed successful GitHub CI run for the exact change SHA",
            }

        objective = str(state.get("objective", "Improve Aurora safely"))
        result = _mission_summary(director_service, phase, objective, state)
        if not result.get("phase_succeeded"):
            result["phase_failed"] = True
            result["error"] = "Director phase returned no successful, complete task evidence"
            return result

        if phase == "modify":
            if not result.get("change_id"):
                result["phase_failed"] = True
                result["phase_succeeded"] = False
                result["error"] = (
                    "modify did not return a successful GitHub file-write receipt on a non-default branch; "
                    "a completed Director mission is not a source-code change"
                )
                return result

        if phase == "ci":
            change_id = str(state.get("change_id", "")).strip().lower()
            if (
                result.get("ci_status") != "success"
                or result.get("ci_head_sha") != change_id
                or not result.get("ci_run_id")
            ):
                result["phase_failed"] = True
                result["phase_succeeded"] = False
                result["error"] = (
                    "GitHub CI did not report completed success for the exact candidate commit SHA"
                )
                return result

        return result

    def rsi_evaluate(state: Mapping[str, Any]) -> str:
        if state.get("phase_failed"):
            return "blocked"
        if (
            state.get("ci_status") == "success"
            and state.get("change_id")
            and state.get("ci_head_sha") == state.get("change_id")
            and state.get("ci_run_id")
        ):
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
