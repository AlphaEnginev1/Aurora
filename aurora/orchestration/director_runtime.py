"""Adaptive 24/7 Director runtime with durable memory and review/replan cycles."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from time import sleep
from typing import Any, Callable
from uuid import uuid4

from .director import DirectorPlanError
from .director_decision_learning import DirectorDecisionLearning
from .director_decision_performance import DirectorDecisionPerformanceStore
from .director_decision_comparison import (
    DirectorDecisionComparativeEvidence,
    DirectorDecisionComparisonEvaluator,
)
from .director_decision_comparison_store import DirectorDecisionComparisonStore
from .director_decision_knowledge import DirectorDecisionKnowledgePromoter
from .director_decision_knowledge_store import DirectorDecisionKnowledgeStore
from .director_decision_policy import DirectorDecisionPolicy
from .director_decision_policy_canary import DirectorDecisionPolicyShadowCanary
from .director_decision_policy_canary_store import DirectorDecisionPolicyShadowCanaryStore
from .director_decision_policy_canary_evaluation import (
    DirectorDecisionPolicyCanaryEvaluator,
    DirectorDecisionPolicyCanaryEvaluation,
)
from .director_decision_policy_canary_evaluation_store import DirectorDecisionPolicyCanaryEvaluationStore
from .director_decision_policy_lifecycle import DirectorDecisionPolicyLifecycle
from .director_decision_policy_lifecycle_store import DirectorDecisionPolicyLifecycleStore
from .director_decision_policy_active_store import DirectorDecisionPolicyActiveStore
from .director_decision_policy_contract_store import DirectorDecisionPolicyContractStore
from .director_decision_policy_proposal_store import DirectorDecisionPolicyProposalStore
from .director_decision_policy_evolution import DirectorDecisionPolicyProposal
from .director_decision_policy_lifecycle import DirectorDecisionPolicyActivation
from .durable_director import DurableDirectorService, MissionSubmission
from .event_bus import DurableEventBus, EventSubscription
from .mission_memory import MissionMemory, MissionMemoryError
from .task_engine import DelegatedTask, DelegatedTaskResult, TaskStatus
from .task_store import DurableTaskStore, TaskEvent, TaskStoreError
from .task_worker import TaskWorker, WorkerCycle, WorkerPolicy
from aurora.skills.evaluation import SkillEvaluator
from aurora.skills.learning import SkillEvolutionEngine, SkillLearningStore
from aurora.skills.autonomous_specialist_acquisition import AutonomousSpecialistAcquisition
from aurora.skills.acquisition_pipeline import PreparedSkillCandidate
from aurora.skills.evolution_runtime import PreparedSkillEvolution, SkillEvolutionRuntime
from aurora.skills.improvement import SkillImprovementGenerator



class DirectorRuntimeError(RuntimeError):
    """Raised when the 24/7 Director runtime cannot operate safely."""


@dataclass(frozen=True)
class RuntimeCycle:
    worker_cycles: tuple[WorkerCycle, ...]
    events: tuple[TaskEvent, ...]
    active_missions: tuple[str, ...]

    @property
    def did_work(self) -> bool:
        return bool(self.events) or any(
            cycle.action != "idle" or cycle.recovered_tasks or cycle.blocked_tasks
            for cycle in self.worker_cycles
        )


@dataclass(frozen=True)
class MissionRuntimeResult:
    submission: MissionSubmission
    status: TaskStatus
    tasks: tuple[DelegatedTaskResult, ...]
    events: tuple[TaskEvent, ...]


class DirectorRuntime:
    """Own continuous mission execution and adaptive Director review."""

    TERMINAL = frozenset({"succeeded", "failed", "blocked"})

    def __init__(
        self,
        *,
        director: DurableDirectorService,
        store: DurableTaskStore,
        event_bus: DurableEventBus | None = None,
        memory: MissionMemory | None = None,
        worker_policy: WorkerPolicy | None = None,
        worker_id: str | None = None,
        event_consumer_id: str = "aurora-director-runtime",
        event_batch_size: int = 100,
        idle_sleep_seconds: float = 0.5,
        worker_concurrency: int = 4,
        max_review_rounds: int = 3,
        skill_evaluator: SkillEvaluator | None = None,
        skill_learning: SkillLearningStore | None = None,
        skill_evolution: SkillEvolutionEngine | None = None,
        autonomous_specialist_acquisition: AutonomousSpecialistAcquisition | None = None,
        skill_evolution_runtime: SkillEvolutionRuntime | None = None,
        skill_improvement_generator: SkillImprovementGenerator | None = None,
        decision_performance: DirectorDecisionPerformanceStore | None = None,
        decision_comparison_store: DirectorDecisionComparisonStore | None = None,
        decision_knowledge_store: DirectorDecisionKnowledgeStore | None = None,
        policy_shadow_activation: DirectorDecisionPolicyActivation | None = None,
        policy_shadow_proposal: DirectorDecisionPolicyProposal | None = None,
        policy_shadow_store: DirectorDecisionPolicyShadowCanaryStore | None = None,
        policy_shadow_evaluation_store: DirectorDecisionPolicyCanaryEvaluationStore | None = None,
        policy_shadow_lifecycle_store: DirectorDecisionPolicyLifecycleStore | None = None,
        policy_shadow_decider: Callable[[str, list[dict[str, Any]], DirectorDecisionPolicyProposal], str] | None = None,
        active_policy_store: DirectorDecisionPolicyActiveStore | None = None,
        policy_proposal_store: DirectorDecisionPolicyProposalStore | None = None,
        policy_contract_store: DirectorDecisionPolicyContractStore | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        if not event_consumer_id.strip():
            raise DirectorRuntimeError("event_consumer_id is empty")
        if event_batch_size < 1:
            raise DirectorRuntimeError("event_batch_size must be at least 1")
        if idle_sleep_seconds < 0:
            raise DirectorRuntimeError("idle_sleep_seconds cannot be negative")
        if max_review_rounds < 1:
            raise DirectorRuntimeError("max_review_rounds must be at least 1")
        if worker_concurrency < 1:
            raise DirectorRuntimeError("worker_concurrency must be at least 1")
        self.director = director
        self.store = store
        self.event_bus = event_bus or DurableEventBus(store)
        self.memory = memory or MissionMemory(store)
        self.worker_policy = worker_policy or WorkerPolicy()
        self.worker_id = worker_id or f"director-worker-{uuid4().hex[:12]}"
        self.event_batch_size = event_batch_size
        self.idle_sleep_seconds = idle_sleep_seconds
        self.max_review_rounds = max_review_rounds
        self.worker_concurrency = worker_concurrency
        self.skill_evaluator = skill_evaluator
        self.skill_learning = skill_learning
        self.skill_evolution = skill_evolution
        self.autonomous_specialist_acquisition = autonomous_specialist_acquisition
        self.skill_evolution_runtime = skill_evolution_runtime
        self.skill_improvement_generator = skill_improvement_generator
        self.decision_performance = decision_performance or DirectorDecisionPerformanceStore(store.path)
        self.decision_comparison_store = (
            decision_comparison_store
            or DirectorDecisionComparisonStore(store.path)
        )
        self.decision_knowledge_store = (
            decision_knowledge_store
            or DirectorDecisionKnowledgeStore(store.path)
        )
        self.decision_knowledge_promoter = DirectorDecisionKnowledgePromoter(
            self.decision_comparison_store
        )
        if (policy_shadow_activation is None) != (policy_shadow_proposal is None):
            raise DirectorRuntimeError(
                "policy shadow activation and proposal must be configured together"
            )
        if policy_shadow_activation is not None and policy_shadow_proposal is not None:
            if policy_shadow_activation.proposal_id != policy_shadow_proposal.proposal_id:
                raise DirectorRuntimeError(
                    "policy shadow activation does not belong to proposal"
                )
            if policy_shadow_activation.state != "canary":
                raise DirectorRuntimeError(
                    "policy shadow activation must remain in canary state"
                )
        if policy_shadow_activation is not None and policy_shadow_decider is None:
            raise DirectorRuntimeError(
                "policy shadow canary requires a candidate decision decider"
            )
        self.policy_shadow_activation = policy_shadow_activation
        self.policy_shadow_proposal = policy_shadow_proposal
        self.policy_shadow_store = policy_shadow_store or DirectorDecisionPolicyShadowCanaryStore(store.path)
        self.policy_shadow_evaluation_store = (
            policy_shadow_evaluation_store
            or DirectorDecisionPolicyCanaryEvaluationStore(store.path)
        )
        self.policy_shadow_lifecycle_store = (
            policy_shadow_lifecycle_store
            or DirectorDecisionPolicyLifecycleStore(store.path)
        )
        self.policy_shadow_decider = policy_shadow_decider
        self.active_policy_store = active_policy_store or DirectorDecisionPolicyActiveStore(store.path)
        self.policy_proposal_store = policy_proposal_store or DirectorDecisionPolicyProposalStore(store.path)
        self.policy_contract_store = policy_contract_store or DirectorDecisionPolicyContractStore(store.path)
        self._sleep = sleep_fn
        self._subscription: EventSubscription = self.event_bus.subscribe(event_consumer_id)
        self._workers: dict[str, TaskWorker] = {}


    def record_decision_comparison(
        self,
        *,
        mission_id: str,
        decision_event_id: int,
        chosen_decision: str,
        alternative: str,
        comparative_evidence: DirectorDecisionComparativeEvidence,
        evidence: tuple[str, ...] = (),
        created_at: str,
    ):
        """Evaluate and durably record explicit comparative evidence.

        Comparative findings remain advisory: they never modify the decision
        performance aggregate and never assert causal superiority.
        """
        result = DirectorDecisionComparisonEvaluator.evaluate(
            decision_event_id=decision_event_id,
            chosen_decision=chosen_decision,
            alternative=alternative,
            chosen_outcome=comparative_evidence.chosen_outcome,
            alternative_outcome=comparative_evidence.alternative_outcome,
            evidence=evidence,
            comparable=True,
            alternative_executed=True,
            comparative_evidence=comparative_evidence,
        )
        self.decision_comparison_store.record(
            decision_event_id=result.decision_event_id,
            chosen_decision=result.chosen_decision,
            alternative=result.alternative,
            source=comparative_evidence.source,
            alternative_execution_id=comparative_evidence.alternative_execution_id,
            chosen_execution_id=comparative_evidence.chosen_execution_id,
            chosen_outcome=comparative_evidence.chosen_outcome,
            alternative_outcome=comparative_evidence.alternative_outcome,
            comparability_basis=comparative_evidence.comparability_basis,
            observed_direction=result.observed_direction,
            causal_attribution=result.causal_attribution,
            state=result.state,
            reason=result.reason,
            evidence=result.evidence,
            created_at=created_at,
        )
        self.event_bus.publish(
            mission_id=mission_id,
            event_type="director_decision_comparison_observed",
            payload={
                "decision_event_id": result.decision_event_id,
                "chosen_decision": result.chosen_decision,
                "alternative": result.alternative,
                "state": result.state,
                "observed_direction": result.observed_direction,
                "causal_attribution": result.causal_attribution,
                "evidence": list(result.evidence),
            },
        )
        return result

    def acquire_specialist(
        self,
        *,
        mission_id: str,
        gap_skill_id: str,
        reason: str,
        required_capabilities: tuple[str, ...] = (),
    ):
        """Discover, construct and prepare a specialist without a manual plan."""
        if self.autonomous_specialist_acquisition is None:
            raise DirectorRuntimeError("autonomous specialist acquisition is not configured")
        gap = self.autonomous_specialist_acquisition.pipeline.runtime.detect_gap(
            mission_id=mission_id,
            skill_id=gap_skill_id,
            reason=reason,
            required_capabilities=required_capabilities,
        )
        prepared = self.autonomous_specialist_acquisition.discover_and_prepare(
            gap=gap,
            registry=self.director.task_engine.skill_registry,
        )
        self.event_bus.publish(
            mission_id=mission_id,
            event_type="skill.acquisition.awaiting_approval",
            payload={
                "candidate_id": prepared.candidate.candidate_id,
                "skill_id": prepared.candidate.skill.skill_id if prepared.candidate.skill else gap_skill_id,
                "source_ids": list(prepared.evidence.source_ids),
                "acquired_tools": list(prepared.evidence.acquired_tools),
                "construction_score": prepared.evidence.construction_score,
                "construction_evidence_state": prepared.evidence.construction_evidence_state,
                "candidate_version": prepared.candidate.candidate_version,
                "state": prepared.evidence.state,
            },
        )
        return prepared

    def approve_specialist(
        self, *, mission_id: str, prepared: PreparedSkillCandidate,
    ):
        """Explicitly approve, promote and re-form the mission around a new specialist."""
        if self.autonomous_specialist_acquisition is None:
            raise DirectorRuntimeError("autonomous specialist acquisition is not configured")
        task_engine = self.director.task_engine
        registry = task_engine.skill_registry
        if registry is None:
            raise DirectorRuntimeError("skill registry is not configured")
        pipeline = self.autonomous_specialist_acquisition.pipeline
        try:
            approved = pipeline.approve(prepared)
            promoted_registry = pipeline.promote(PreparedSkillCandidate(approved, prepared.evidence), registry)
        except Exception as exc:
            self.event_bus.publish(mission_id=mission_id, event_type="skill.acquisition.promotion_failed",
                payload={"candidate_id": prepared.candidate.candidate_id, "skill_id": prepared.candidate.skill_id, "error": str(exc)})
            raise DirectorRuntimeError(f"specialist promotion failed: {exc}") from exc
        task_engine.skill_registry = promoted_registry
        pending = tuple(item.task for item in self.store.list_tasks(mission_id) if item.status in {"pending", "running"})
        team_payload = {"candidate_id": approved.candidate_id, "skill_id": approved.skill_id, "version": approved.candidate_version, "promoted": True}
        self.event_bus.publish(mission_id=mission_id, event_type="skill.acquisition.promoted", payload=team_payload)
        coordinator = getattr(self.director, "team_coordinator", None)
        if coordinator is not None and pending:
            objective = self._objective_from_memory(mission_id)
            try:
                state = coordinator.prepare(mission_id=mission_id, objective=objective, tasks=pending)
            except Exception as exc:
                self.event_bus.publish(mission_id=mission_id, event_type="team.reformation_failed", payload={"candidate_id": approved.candidate_id, "error": str(exc)})
                raise DirectorRuntimeError(f"team reformation failed: {exc}") from exc
            self.event_bus.publish(mission_id=mission_id, event_type="team.reformed_after_skill_promotion", payload={
                **team_payload, "roles": [member.role for member in state.team.members],
                "required_skills": list(state.team.required_skills), "covered_skills": list(state.team.covered_skills),
                "missing_skills": list(state.team.missing_skills),
                "added_roles": list(state.reconfiguration.added_roles) if state.reconfiguration else [],
                "removed_roles": list(state.reconfiguration.removed_roles) if state.reconfiguration else [],
            })
            self.memory.append(mission_id=mission_id, source="director", kind="skill_promotion",
                content=json.dumps(team_payload, sort_keys=True), metadata={"candidate_id": approved.candidate_id})
            return state
        self.event_bus.publish(mission_id=mission_id, event_type="team.reformation_skipped",
            payload={"candidate_id": approved.candidate_id, "reason": "no pending tasks or team coordinator"})
        return promoted_registry
    def prepare_skill_evolution(
        self,
        *,
        mission_id: str,
        proposal_id: str,
        improved_instructions: str | None = None,
    ) -> PreparedSkillEvolution:
        """Prepare a versioned evolution candidate from durable learning evidence."""
        if self.skill_learning is None or self.skill_evolution_runtime is None:
            raise DirectorRuntimeError("skill evolution runtime is not configured")
        registry = self.director.task_engine.skill_registry
        if registry is None:
            raise DirectorRuntimeError("skill registry is not configured")
        proposals = self.skill_learning.pending_proposals()
        proposal = next((item for item in proposals if item.proposal_id == proposal_id), None)
        if proposal is None:
            raise DirectorRuntimeError(f"unknown pending skill evolution proposal: {proposal_id}")
        generated_model = None
        generated_evidence = None
        if improved_instructions is None and self.skill_improvement_generator is not None:
            active = registry.skills.get(proposal.skill_id)
            if active is None:
                raise DirectorRuntimeError(f"active skill is unavailable: {proposal.skill_id}")
            try:
                generated = self.skill_improvement_generator.generate(active=active, proposal=proposal)
            except Exception as exc:
                self.event_bus.publish(
                    mission_id=mission_id,
                    event_type="skill.evolution.generation_failed",
                    payload={"proposal_id": proposal.proposal_id, "skill_id": proposal.skill_id, "error": str(exc)},
                )
                raise DirectorRuntimeError(f"skill improvement generation failed: {exc}") from exc
            improved_instructions = generated.instructions
            generated_model = generated.model
            generated_evidence = generated.evidence_summary
        prepared = self.skill_evolution_runtime.prepare(
            proposal=proposal,
            registry=registry,
            improved_instructions=improved_instructions,
        )
        self.event_bus.publish(
            mission_id=mission_id,
            event_type="skill.evolution.awaiting_approval",
            payload={
                "candidate_id": prepared.candidate_id,
                "proposal_id": proposal.proposal_id,
                "skill_id": prepared.skill.skill_id,
                "base_version": proposal.base_version,
                "candidate_version": prepared.skill.version,
                "evidence_count": proposal.evidence_count,
                "reason": proposal.reason,
                "suggested_change": proposal.suggested_change,
                "generation_model": generated_model,
                "generation_evidence": generated_evidence,
                "validation": (
                    {
                        "baseline_score": prepared.validation.baseline_score,
                        "candidate_score": prepared.validation.candidate_score,
                        "improvement": prepared.validation.improvement,
                        "model": prepared.validation.model,
                        "state": prepared.validation.state,
                        "case_count": len(prepared.validation.cases),
                        "evidence_complete_cases": sum(
                            1 for case in prepared.validation.cases if case.evidence_complete
                        ),
                    }
                    if prepared.validation is not None
                    else None
                ),
            },
        )
        return prepared

    def approve_skill_evolution(
        self,
        *,
        mission_id: str,
        prepared: PreparedSkillEvolution,
    ):
        """Explicitly approve, promote and re-form the mission after skill evolution."""
        if self.skill_evolution_runtime is None:
            raise DirectorRuntimeError("skill evolution runtime is not configured")
        registry = self.director.task_engine.skill_registry
        if registry is None:
            raise DirectorRuntimeError("skill registry is not configured")
        try:
            approved = self.skill_evolution_runtime.approve(prepared)
            promoted = self.skill_evolution_runtime.promote(approved, registry)
        except Exception as exc:
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="skill.evolution.promotion_failed",
                payload={
                    "candidate_id": prepared.candidate_id,
                    "skill_id": prepared.skill.skill_id,
                    "error": str(exc),
                },
            )
            raise DirectorRuntimeError(f"skill evolution promotion failed: {exc}") from exc

        self.director.task_engine.skill_registry = promoted
        payload = {
            "candidate_id": approved.candidate_id,
            "proposal_id": approved.proposal.proposal_id,
            "skill_id": approved.skill.skill_id,
            "version": approved.skill.version,
            "base_version": approved.proposal.base_version,
            "promoted": True,
            "validation": (
                {
                    "baseline_score": approved.validation.baseline_score,
                    "candidate_score": approved.validation.candidate_score,
                    "improvement": approved.validation.improvement,
                    "model": approved.validation.model,
                    "state": approved.validation.state,
                }
                if approved.validation is not None
                else None
            ),
        }
        self.event_bus.publish(
            mission_id=mission_id,
            event_type="skill.evolution.promoted",
            payload=payload,
        )

        pending = tuple(
            item.task
            for item in self.store.list_tasks(mission_id)
            if item.status in {"pending", "running"}
        )
        coordinator = getattr(self.director, "team_coordinator", None)
        if coordinator is not None and pending:
            objective = self._objective_from_memory(mission_id)
            try:
                state = coordinator.prepare(
                    mission_id=mission_id,
                    objective=objective,
                    tasks=pending,
                )
            except Exception as exc:
                self.event_bus.publish(
                    mission_id=mission_id,
                    event_type="team.reformation_failed",
                    payload={"candidate_id": approved.candidate_id, "error": str(exc)},
                )
                raise DirectorRuntimeError(f"team reformation after evolution failed: {exc}") from exc
            delta = state.reconfiguration
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="team.reformed_after_skill_evolution",
                payload={
                    **payload,
                    "roles": [member.role for member in state.team.members],
                    "required_skills": list(state.team.required_skills),
                    "covered_skills": list(state.team.covered_skills),
                    "missing_skills": list(state.team.missing_skills),
                    "added_roles": list(delta.added_roles) if delta else [],
                    "removed_roles": list(delta.removed_roles) if delta else [],
                },
            )
            self.memory.append(
                mission_id=mission_id,
                source="director",
                kind="skill_evolution",
                content=json.dumps(payload, sort_keys=True),
                metadata={"candidate_id": approved.candidate_id},
            )
            return state

        self.event_bus.publish(
            mission_id=mission_id,
            event_type="team.reformation_skipped",
            payload={"candidate_id": approved.candidate_id, "reason": "no pending tasks or team coordinator"},
        )
        return promoted

    def rollback_skill_evolution(
        self, *, mission_id: str, candidate_id: str,
    ):
        """Restore the exact pre-evolution skill snapshot after a verified regression."""
        if self.skill_evolution_runtime is None:
            raise DirectorRuntimeError("skill evolution runtime is not configured")
        registry = self.director.task_engine.skill_registry
        if registry is None:
            raise DirectorRuntimeError("skill registry is not configured")
        try:
            restored = self.skill_evolution_runtime.rollback(candidate_id, registry)
        except Exception as exc:
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="skill.evolution.rollback_failed",
                payload={"candidate_id": candidate_id, "error": str(exc)},
            )
            raise DirectorRuntimeError(f"skill evolution rollback failed: {exc}") from exc
        self.director.task_engine.skill_registry = restored
        self.event_bus.publish(
            mission_id=mission_id,
            event_type="skill.evolution.rolled_back",
            payload={"candidate_id": candidate_id, "restored": True},
        )
        return restored

    def evaluate_policy_shadow_canary(
        self,
        *,
        rollback_on_integrity_violation: bool = True,
        rollback_authority: str = "shadow-canary-guard",
        min_observations: int = 5,
    ) -> DirectorDecisionPolicyCanaryEvaluation | None:
        """Evaluate the configured shadow canary and optionally roll it back on integrity violation."""
        activation = self.policy_shadow_activation
        proposal = self.policy_shadow_proposal
        if activation is None or proposal is None:
            return None
        evaluation = DirectorDecisionPolicyCanaryEvaluator.evaluate(
            self.policy_shadow_store,
            proposal.proposal_id,
            min_observations=min_observations,
        )
        self.policy_shadow_evaluation_store.record(evaluation)
        if (
            rollback_on_integrity_violation
            and evaluation.rollback_required
            and activation.state == "canary"
        ):
            rolled_back = DirectorDecisionPolicyLifecycle.rollback(
                activation,
                reason=evaluation.reason,
                authorized_by=rollback_authority,
            )
            self.policy_shadow_lifecycle_store.record_activation(rolled_back)
            self.policy_shadow_activation = rolled_back
        return evaluation

    def _observe_policy_shadow_canary(
        self,
        *,
        mission_id: str,
        decision_event_id: int,
        objective: str,
        context: list[dict[str, Any]],
        baseline_decision: str,
    ) -> None:
        """Run the candidate in shadow only when a terminal baseline outcome exists."""
        activation = self.policy_shadow_activation
        proposal = self.policy_shadow_proposal
        decider = self.policy_shadow_decider
        if activation is None or proposal is None or decider is None:
            return
        if self.store.mission_status(mission_id) not in self.TERMINAL:
            return
        shadow_context = list(context)
        shadow_context.append(
            {
                "role": "user",
                "content": (
                    "SHADOW POLICY SIMULATION ONLY. The active baseline decision is already "
                    "the executed policy. Apply this proposed policy change only to determine "
                    "what decision a candidate would recommend for the same observed state. "
                    "Do not execute candidate tasks, do not modify mission state, and return a "
                    "decision for observation only. Proposed policy change: "
                    + proposal.proposed_change
                ),
            }
        )
        try:
            candidate_decision = decider(objective, shadow_context, proposal).strip()
        except Exception as exc:
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="director_decision_policy_shadow_failed",
                payload={
                    "decision_event_id": decision_event_id,
                    "proposal_id": proposal.proposal_id,
                    "error": str(exc),
                },
            )
            return
        if candidate_decision not in {"complete", "continue", "blocked"}:
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="director_decision_policy_shadow_failed",
                payload={
                    "decision_event_id": decision_event_id,
                    "proposal_id": proposal.proposal_id,
                    "error": f"invalid candidate decision: {candidate_decision}",
                },
            )
            return
        status = self.store.mission_status(mission_id)
        actual_outcome = f"mission_{status}"
        observation = DirectorDecisionPolicyShadowCanary.observe(
            activation,
            decision_event_id=decision_event_id,
            baseline_decision=baseline_decision,
            candidate_decision=candidate_decision,
            actual_outcome=actual_outcome,
        )
        self.policy_shadow_store.record(observation)
        self.event_bus.publish(
            mission_id=mission_id,
            event_type="director_decision_policy_shadow_observed",
            payload={
                "observation_id": observation.observation_id,
                "decision_event_id": observation.decision_event_id,
                "proposal_id": observation.proposal_id,
                "validation_id": observation.validation_id,
                "baseline_decision": observation.baseline_decision,
                "candidate_decision": observation.candidate_decision,
                "actual_outcome": observation.actual_outcome,
                "decision_agreement": observation.decision_agreement,
                "candidate_executed": observation.candidate_executed,
                "state": observation.state,
            },
        )
        evaluation = self.evaluate_policy_shadow_canary()
        if evaluation is not None:
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="director_decision_policy_shadow_evaluated",
                payload={
                    "evaluation_id": evaluation.evaluation_id,
                    "proposal_id": evaluation.proposal_id,
                    "observations": evaluation.observations,
                    "agreements": evaluation.agreements,
                    "disagreements": evaluation.disagreements,
                    "agreement_rate": evaluation.agreement_rate,
                    "governance_review_ready": evaluation.governance_review_ready,
                    "rollback_required": evaluation.rollback_required,
                    "state": evaluation.state,
                    "reason": evaluation.reason,
                },
            )

    def submit_mission(self, **kwargs: Any) -> MissionSubmission:
        """Plan and durably submit a mission for the continuous worker loop."""
        submission = self.director.submit_mission(**kwargs)
        try:
            self.memory.append(
                mission_id=submission.mission_id,
                source="director",
                kind="mission_objective",
                content=submission.plan.objective,
                metadata={"model": submission.plan.model},
            )
            self.memory.append(
                mission_id=submission.mission_id,
                source="director",
                kind="mission_plan",
                content=self._plan_text(submission.plan.tasks),
                metadata={"task_count": len(submission.plan.tasks)},
            )
        except MissionMemoryError as exc:
            raise DirectorRuntimeError(
                f"mission submitted but memory could not be initialized: {exc}"
            ) from exc
        return submission

    def run_once(self) -> RuntimeCycle:
        """Consume events, execute active tasks, and adapt terminal missions."""
        events = self.event_bus.poll(
            self._subscription,
            limit=self.event_batch_size,
        )
        event_missions = {event.mission_id for event in events}

        known_missions = set(self.store.list_missions())
        mission_ids = known_missions | (event_missions & known_missions)
        active = [
            mission_id
            for mission_id in sorted(mission_ids)
            if self.store.mission_status(mission_id) not in self.TERMINAL
        ]
        terminal_candidates = [
            mission_id
            for mission_id in sorted(mission_ids)
            if self.store.mission_status(mission_id) in self.TERMINAL
            and not self._is_finalized(mission_id)
        ]

        for mission_id in active:
            if not self.store.has_event(mission_id, "mission_started"):
                self.event_bus.publish(
                    mission_id=mission_id,
                    event_type="mission_started",
                    payload={"worker_id": self.worker_id},
                )

        assignments = [
            (mission_id, slot)
            for mission_id in active
            for slot in range(self.worker_concurrency)
        ]
        cycles: list[WorkerCycle] = []
        if assignments:
            with ThreadPoolExecutor(
                max_workers=min(self.worker_concurrency, len(assignments)),
                thread_name_prefix="aurora-worker",
            ) as pool:
                futures = [
                    pool.submit(self._worker_for(mission_id, slot).run_once)
                    for mission_id, slot in assignments
                ]
                for (mission_id, _slot), future in zip(assignments, futures):
                    try:
                        cycle = future.result()
                    except Exception as exc:
                        cycle = WorkerCycle(action="error")
                        self.event_bus.publish(
                            mission_id=mission_id,
                            event_type="worker.cycle_failed",
                            payload={"worker_id": self.worker_id, "error": str(exc)},
                        )
                    cycles.append(cycle)
                    self._record_worker_result(mission_id, cycle)
                    if self.store.mission_status(mission_id) in self.TERMINAL:
                        terminal_candidates.append(mission_id)

        for mission_id in sorted(set(terminal_candidates)):
            self._review_or_finalize(mission_id)

        for event in events:
            if event.event_type in {"skill.evaluated", "skill.evolution_proposed", "team.adapted_runtime", "team.adaptation_failed"}:
                try:
                    self.memory.append(
                        mission_id=event.mission_id,
                        task_id=event.task_id,
                        source="skill-learning",
                        kind=event.event_type,
                        content=json.dumps(event.payload, sort_keys=True),
                        metadata={"event_id": event.event_id},
                    )
                except MissionMemoryError:
                    pass
            self.event_bus.ack(self._subscription, event.event_id)

        return RuntimeCycle(
            worker_cycles=tuple(cycles),
            events=events,
            active_missions=tuple(active),
        )

    def run_until_terminal(
        self,
        submission: MissionSubmission,
        *,
        max_cycles: int = 1000,
    ) -> MissionRuntimeResult:
        if max_cycles < 1:
            raise DirectorRuntimeError("max_cycles must be at least 1")
        observed: list[TaskEvent] = []
        for _ in range(max_cycles):
            cycle = self.run_once()
            observed.extend(cycle.events)
            if self._is_finalized(submission.mission_id):
                return MissionRuntimeResult(
                    submission=submission,
                    status=self._final_status(submission.mission_id),
                    tasks=self._results_snapshot(submission.mission_id),
                    events=tuple(observed) + self._mission_events(submission.mission_id),
                )
        raise DirectorRuntimeError(
            f"mission did not reach final state within max_cycles: {submission.mission_id}"
        )

    def run_forever(self, stop_event: Event | None = None) -> None:
        """Run continuously until the supplied stop event is set."""
        stop = stop_event or Event()
        while not stop.is_set():
            cycle = self.run_once()
            if not cycle.did_work and self.idle_sleep_seconds:
                self._sleep(self.idle_sleep_seconds)

    def _worker_for(self, mission_id: str, slot: int = 0) -> TaskWorker:
        key = f"{mission_id}:{slot}"
        worker = self._workers.get(key)
        if worker is None:
            worker = TaskWorker(
                store=self.store,
                task_engine=self.director.task_engine,
                mission_id=mission_id,
                worker_id=f"{self.worker_id}:{mission_id}:{slot}",
                policy=self.worker_policy,
                event_bus=self.event_bus,
                skill_evaluator=self.skill_evaluator,
                skill_learning=self.skill_learning,
                skill_evolution=self.skill_evolution,
            )
            self._workers[key] = worker
        return worker

    def _record_worker_result(self, mission_id: str, cycle: WorkerCycle) -> None:
        if not cycle.task_id or cycle.action not in {"succeeded", "failed", "retried"}:
            return
        stored = self.store.get(mission_id, cycle.task_id)
        if stored is None:
            return
        result = stored.result or {}
        if cycle.action in {"failed", "retried"}:
            self._adapt_team_after_runtime_signal(mission_id, stored.task.task_id)
        try:
            self.memory.append(
                mission_id=mission_id,
                source=f"worker:{self.worker_id}",
                kind="task_result",
                content=self._result_text(stored.task.task_id, stored.status, result, stored.last_error),
                metadata={
                    "task_id": stored.task.task_id,
                    "attempt": stored.attempts,
                    "status": stored.status,
                    "action": cycle.action,
                },
            )
        except MissionMemoryError as exc:
            self.event_bus.publish(
                mission_id=mission_id,
                task_id=stored.task.task_id,
                event_type="memory_write_failed",
                payload={"error": str(exc)},
            )

    def _adapt_team_after_runtime_signal(self, mission_id: str, trigger_task_id: str) -> None:
        coordinator = getattr(self.director, "team_coordinator", None)
        if coordinator is None:
            return
        objective = self._objective_from_memory(mission_id)
        pending = tuple(
            item.task
            for item in self.store.list_tasks(mission_id)
            if item.status in {"pending", "running"}
        )
        if not pending:
            return
        try:
            state = coordinator.adapt_for_runtime(
                mission_id=mission_id,
                objective=objective,
                current_team=None,
                pending_tasks=pending,
                trigger_task_id=trigger_task_id,
            )
        except Exception as exc:
            self.event_bus.publish(
                mission_id=mission_id,
                task_id=trigger_task_id,
                event_type="team.adaptation_failed",
                payload={"error": str(exc), "trigger": trigger_task_id},
            )
            return
        delta = state.reconfiguration
        self.event_bus.publish(
            mission_id=mission_id,
            task_id=trigger_task_id,
            event_type="team.adapted_runtime",
            payload={
                "trigger": trigger_task_id,
                "roles": [member.role for member in state.team.members],
                "required_skills": list(state.team.required_skills),
                "covered_skills": list(state.team.covered_skills),
                "missing_skills": list(state.team.missing_skills),
                "added_roles": list(delta.added_roles) if delta else [],
                "removed_roles": list(delta.removed_roles) if delta else [],
            },
        )

    def _review_or_finalize(self, mission_id: str) -> None:
        if self._is_finalized(mission_id):
            return

        self._evaluate_pending_director_decisions(mission_id)

        round_number = self._review_rounds(mission_id) + 1
        if round_number > self.max_review_rounds:
            self._publish_terminal(
                mission_id,
                status="blocked",
                reason="maximum Director review rounds exhausted",
            )
            return

        objective = self._objective_from_memory(mission_id)
        context = self.memory.context(mission_id, limit=50)
        context.append(
            {
                "role": "user",
                "content": self._task_state_text(mission_id),
            }
        )
        team_context = self._team_state_text(mission_id)
        if team_context:
            context.append({"role": "user", "content": team_context})
        routing_evidence = self._routing_evidence(mission_id)
        decision_learning = DirectorDecisionLearning.learned_evidence(
            self.store.events(mission_id)
        )
        decision_performance = self.decision_performance.summary()
        decision_guidance = DirectorDecisionPolicy.guidance(decision_performance)
        if routing_evidence:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "Immutable routing evidence for Director review: "
                        + json.dumps(routing_evidence, sort_keys=True)
                    ),
                }
            )
        if decision_learning:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "Durable Director decision learning evidence: "
                        + json.dumps(decision_learning, sort_keys=True)
                    ),
                }
            )
            pending_evidence = [
                item
                for item in decision_learning
                if item.get("evidence_completeness") in {"partial", "insufficient"}
            ]
            if pending_evidence:
                context.append(
                    {
                        "role": "user",
                        "content": (
                            "Decision outcome observed, but evidence insufficient to establish "
                            "decision quality. Treat this as advisory evaluation_pending evidence; "
                            "do not convert it into an automatic decision rule: "
                            + json.dumps(pending_evidence, sort_keys=True)
                        ),
                    }
                )
        if decision_performance:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "Aggregated Director decision performance: "
                        + json.dumps(
                            [
                                {
                                    "decision": item.decision,
                                    "evaluations": item.evaluations,
                                    "average_score": item.average_score,
                                    "successful_evaluations": item.successful_evaluations,
                                    "success_rate": item.success_rate,
                                }
                                for item in decision_performance
                            ],
                            sort_keys=True,
                        )
                    ),
                }
            )
        decision_event_id = max(
            (
                int(item["decision_event_id"])
                for item in decision_learning
                if item.get("decision_event_id") is not None
            ),
            default=None,
        )
        comparisons = (
            self.decision_comparison_store.list_for_decision(decision_event_id)
            if decision_event_id is not None
            else ()
        )
        comparison_summaries = (
            self.decision_comparison_store.summarize_for_decision(decision_event_id)
            if decision_event_id is not None
            else ()
        )
        # Replication is a cross-decision property. Do not scope its
        # discovery to the single latest evaluated decision: a mission may
        # have accumulated comparisons on earlier Director review events
        # before the terminal evaluation creates a newer decision id.
        replication_summaries = []
        seen_replication = set()
        review_event_ids = {
            int(event.event_id)
            for event in self.store.events(mission_id)
            if event.event_type == "director_review"
        }
        for review_event_id in review_event_ids:
            for item in self.decision_comparison_store.list_for_decision(review_event_id):
                key = (item.alternative, item.chosen_decision, item.comparability_basis)
                if key in seen_replication:
                    continue
                seen_replication.add(key)
                replication_summaries.extend(
                    self.decision_comparison_store.summarize_across_decisions(
                        alternative=item.alternative,
                        chosen_decision=item.chosen_decision,
                        comparability_basis=item.comparability_basis,
                    )
                )
        if comparisons:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "Comparative evidence advisory for this Director decision. "
                        "These observations are not causal claims and must not be converted "
                        "into an automatic rule: "
                        + json.dumps(
                            [
                                {
                                    "alternative": item.alternative,
                                    "source": item.source,
                                    "state": item.state,
                                    "observed_direction": item.observed_direction,
                                    "reason": item.reason,
                                    "causal_attribution": item.causal_attribution,
                                    "evidence": item.evidence,
                                }
                                for item in comparisons
                            ],
                            sort_keys=True,
                        )
                    ),
                }
            )
        if comparison_summaries:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "Aggregated comparative observations for this Director decision. "
                        "This is repeated observational evidence only; it does not establish "
                        "causality or an automatic policy rule: "
                        + json.dumps(
                            [
                                {
                                    "alternative": item.alternative,
                                    "chosen_decision": item.chosen_decision,
                                    "observations": item.observations,
                                    "better_observations": item.better_observations,
                                    "worse_observations": item.worse_observations,
                                    "same_observations": item.same_observations,
                                    "undetermined_observations": item.undetermined_observations,
                                    "consistent_direction": item.consistent_direction,
                                    "sources": item.sources,
                                }
                                for item in comparison_summaries
                            ],
                            sort_keys=True,
                        )
                    ),
                }
            )
        if replication_summaries:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "Cross-decision comparative replication advisory. "
                        "Repeated observations across distinct decisions with the same "
                        "comparability basis establish consistency only; they do not "
                        "establish causality or authorize an automatic policy rule: "
                        + json.dumps(
                            [
                                {
                                    "alternative": item.alternative,
                                    "chosen_decision": item.chosen_decision,
                                    "comparability_basis": item.comparability_basis,
                                    "decision_events": item.decision_events,
                                    "observations": item.observations,
                                    "better_observations": item.better_observations,
                                    "worse_observations": item.worse_observations,
                                    "same_observations": item.same_observations,
                                    "undetermined_observations": item.undetermined_observations,
                                    "consistent_direction": item.consistent_direction,
                                    "sources": item.sources,
                                }
                                for item in replication_summaries
                            ],
                            sort_keys=True,
                        )
                    ),
                }
            )
        promoted_knowledge = []
        seen_knowledge = set()
        for summary in replication_summaries:
            knowledge_key = (
                summary.alternative,
                summary.chosen_decision,
                summary.comparability_basis,
            )
            if knowledge_key in seen_knowledge:
                continue
            seen_knowledge.add(knowledge_key)
            promotion = self.decision_knowledge_promoter.promote(summary)
            if not promotion.promoted or promotion.knowledge is None:
                continue
            self.decision_knowledge_store.record(promotion.knowledge)
            promoted_knowledge.append(promotion.knowledge)

        if promoted_knowledge:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "Promoted empirical Director knowledge advisory. "
                        "This knowledge was promoted only from replicated, directionally "
                        "consistent comparable observations. It is observational evidence "
                        "and does not establish causality or authorize an automatic policy "
                        "change: "
                        + json.dumps(
                            [
                                {
                                    "knowledge_id": item.knowledge_id,
                                    "alternative": item.alternative,
                                    "chosen_decision": item.chosen_decision,
                                    "comparability_basis": item.comparability_basis,
                                    "observed_direction": item.observed_direction,
                                    "decision_events": item.decision_events,
                                    "observations": item.observations,
                                    "sources": item.sources,
                                    "state": item.state,
                                }
                                for item in promoted_knowledge
                            ],
                            sort_keys=True,
                        )
                    ),
                }
            )
        policy_prompt = DirectorDecisionPolicy.prompt(decision_guidance)
        if policy_prompt:
            context.append({"role": "user", "content": policy_prompt})
        active_policy = self.active_policy_store.current()
        if active_policy is not None:
            active_proposal = self.policy_proposal_store.get(active_policy.proposal_id)
            if active_proposal is not None:
                try:
                    active_policy_contract = DirectorDecisionPolicy.active_contract(
                        active_policy,
                        active_proposal,
                    )
                    self.policy_contract_store.record(active_policy_contract)
                    active_policy_prompt = DirectorDecisionPolicy.active_prompt(
                        active_policy,
                        active_proposal,
                    )
                except (ValueError, DirectorDecisionPolicyContractError) as exc:
                    self.event_bus.publish(
                        mission_id=mission_id,
                        event_type="director_active_policy_invalid",
                        payload={
                            "proposal_id": active_policy.proposal_id,
                            "error": str(exc),
                        },
                    )
                else:
                    context.append({"role": "user", "content": active_policy_prompt})
        self.event_bus.publish(
            mission_id=mission_id,
            event_type="director_review_started",
            payload={"round": round_number, "worker_id": self.worker_id},
        )
        try:
            review = self.director.planner.review(
                objective=objective,
                context=context,
            )
        except (DirectorPlanError, RuntimeError, ValueError) as exc:
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="director_review_failed",
                payload={"round": round_number, "error": str(exc)},
            )
            if round_number >= self.max_review_rounds:
                self._publish_terminal(
                    mission_id,
                    status="blocked",
                    reason=f"Director review failed: {exc}",
                )
            return

        review_event = self.event_bus.publish(
            mission_id=mission_id,
            event_type="director_review",
            payload={
                "round": round_number,
                "decision": review.decision,
                "rationale": review.rationale,
                "model": review.model,
                "routing_evidence": routing_evidence,
                "considered_alternatives": list(review.considered_alternatives),
            },
        )
        self._observe_policy_shadow_canary(
            mission_id=mission_id,
            decision_event_id=review_event.event_id,
            objective=objective,
            context=context,
            baseline_decision=review.decision,
        )
        try:
            self.memory.append(
                mission_id=mission_id,
                source="director",
                kind="review",
                content=f"Decision={review.decision}; rationale={review.rationale}",
                metadata={
                    "round": round_number,
                    "model": review.model,
                    "routing_evidence": routing_evidence,
                    "considered_alternatives": list(review.considered_alternatives),
                },
            )
        except MissionMemoryError:
            pass

        if review.decision == "complete":
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="director_decision_executed",
                payload={
                    "round": round_number,
                    "decision": review.decision,
                    "execution": "mission_terminal",
                },
            )
            self._publish_terminal(
                mission_id,
                status="succeeded",
                reason=review.rationale,
            )
            self._evaluate_pending_director_decisions(mission_id)
            return
        if review.decision == "blocked":
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="director_decision_executed",
                payload={
                    "round": round_number,
                    "decision": review.decision,
                    "execution": "mission_terminal",
                },
            )
            self._publish_terminal(
                mission_id,
                status="blocked",
                reason=review.rationale,
            )
            self._evaluate_pending_director_decisions(mission_id)
            return

        tasks = self._phase_tasks(mission_id, round_number, review.tasks)
        try:
            coordinator = getattr(self.director, "team_coordinator", None)
            if coordinator is not None:
                team_state = coordinator.prepare(
                    mission_id=mission_id,
                    objective=objective,
                    tasks=tasks,
                )
                validated = team_state.tasks
                self.event_bus.publish(
                    mission_id=mission_id,
                    event_type="team.reconfigured",
                    payload={
                        "round": round_number,
                        "roles": [member.role for member in team_state.team.members],
                        "required_skills": list(team_state.team.required_skills),
                        "covered_skills": list(team_state.team.covered_skills),
                    },
                )
            else:
                validated = self.director.task_engine.validate(tasks)
            self.store.enqueue(mission_id, validated, phase=round_number)
        except (TaskStoreError, ValueError) as exc:
            self._publish_terminal(
                mission_id,
                status="blocked",
                reason=f"Director follow-up plan rejected: {exc}",
            )
            return

        self.event_bus.publish(
            mission_id=mission_id,
            event_type="director_decision_executed",
            payload={
                "round": round_number,
                "decision": review.decision,
                "execution": "follow_up_tasks_enqueued",
                "task_ids": [task.task_id for task in validated],
            },
        )
        self.event_bus.publish(
            mission_id=mission_id,
            event_type="mission_reopened",
            payload={
                "round": round_number,
                "task_ids": [task.task_id for task in validated],
                "rationale": review.rationale,
            },
        )
        try:
            self.memory.append(
                mission_id=mission_id,
                source="director",
                kind="follow_up_plan",
                content=self._plan_text(validated),
                metadata={"round": round_number, "task_count": len(validated)},
            )
        except MissionMemoryError:
            pass

    def _evaluate_pending_director_decisions(self, mission_id: str) -> None:
        events = self.store.events(mission_id)
        evaluated_ids = {
            int(event.payload.get("decision_event_id"))
            for event in events
            if event.event_type == "director_decision_evaluated"
            and event.payload.get("decision_event_id") is not None
        }
        terminal_types = {
            "mission_succeeded",
            "mission_failed",
            "mission_blocked",
        }
        terminal_event = next(
            (event for event in reversed(events) if event.event_type in terminal_types),
            None,
        )
        durable_terminal_type = None
        if terminal_event is None:
            durable_status = self.store.mission_status(mission_id)
            if durable_status in self.TERMINAL:
                durable_terminal_type = f"mission_{durable_status}"
        for decision_event in events:
            if decision_event.event_type != "director_review":
                continue
            if decision_event.event_id in evaluated_ids:
                continue
            # Do not finalize a decision evaluation before a terminal
            # result is observed either as a durable mission event or as the
            # authoritative terminal task state.
            if terminal_event is None and durable_terminal_type is None:
                continue
            # Scope evidence to this decision's review interval. A later
            # Director review must never satisfy an earlier decision's
            # execution/evidence requirements.
            observed_events = []
            decision_task_ids: set[str] = set()
            interval_closed = False
            for event in events:
                if event.event_id <= decision_event.event_id:
                    continue
                if event.event_type == "director_review":
                    interval_closed = True
                    break
                observed_events.append(event.event_type)
                if event.event_type == "director_decision_executed":
                    decision_task_ids.update(
                        str(task_id)
                        for task_id in (event.payload.get("task_ids") or [])
                    )

            # Follow-up tasks belong to the decision that created them even
            # when the next Director review happens before those tasks finish.
            # Associate their durable terminal events by task_id instead of
            # extending the generic evidence window across later decisions.
            durable_tasks = self.store.list_tasks(mission_id)
            decision_round = int(decision_event.payload.get("round", 0))
            phase_tasks = [
                task
                for task in durable_tasks
                if task.phase == decision_round and decision_round > 0
            ]
            if not decision_task_ids:
                decision_task_ids.update(task.task.task_id for task in phase_tasks)
            if decision_task_ids:
                # The durable task row is authoritative even when its terminal
                # journal event falls after a subsequent Director review.
                for task in durable_tasks:
                    if task.task.task_id in decision_task_ids and task.status in {
                        "succeeded",
                        "failed",
                        "blocked",
                    }:
                        observed_events.append(task.status)
            if phase_tasks and all(
                task.status in {"succeeded", "failed", "blocked"}
                for task in phase_tasks
            ):
                if any(task.status == "failed" for task in phase_tasks):
                    observed_events.append("task_failed")
                elif any(task.status == "blocked" for task in phase_tasks):
                    observed_events.append("task_blocked")
                else:
                    observed_events.append("task_completed")

            if terminal_event is not None and terminal_event.event_id > decision_event.event_id:
                observed_events.append(terminal_event.event_type)
            elif durable_terminal_type is not None:
                observed_events.append(durable_terminal_type)
            evaluation = DirectorDecisionLearning.evaluate(
                decision_event=decision_event,
                observed_event_types=observed_events,
            )
            if evaluation is None:
                continue
            self.event_bus.publish(
                mission_id=mission_id,
                event_type="director_decision_evaluated",
                payload={
                    "decision_event_id": evaluation.decision_event_id,
                    "round": evaluation.round,
                    "decision": evaluation.decision,
                    "score": evaluation.score,
                    "outcome": evaluation.outcome,
                    "quality": evaluation.quality,
                    "evidence_state": evaluation.evidence_state,
                    "attribution": evaluation.attribution,
                    "evidence_completeness": evaluation.evidence_completeness,
                    "sufficiency_reasons": list(evaluation.sufficiency_reasons),
                    "evidence": list(evaluation.evidence),
                },
            )
            self.decision_performance.record(
                decision_event_id=evaluation.decision_event_id,
                round=evaluation.round,
                decision=evaluation.decision,
                score=evaluation.score,
                outcome=evaluation.outcome,
                evidence=evaluation.evidence,
                evidence_completeness=evaluation.evidence_completeness,
                sufficiency_reasons=evaluation.sufficiency_reasons,
                created_at=decision_event.created_at,
            )
            try:
                self.memory.append(
                    mission_id=mission_id,
                    source="director-learning",
                    kind="decision_evaluation",
                    content=json.dumps(
                        {
                            "decision": evaluation.decision,
                            "score": evaluation.score,
                            "outcome": evaluation.outcome,
                            "quality": evaluation.quality,
                            "evidence_state": evaluation.evidence_state,
                            "attribution": evaluation.attribution,
                            "evidence_completeness": evaluation.evidence_completeness,
                            "sufficiency_reasons": list(evaluation.sufficiency_reasons),
                        },
                        sort_keys=True,
                    ),
                    metadata={
                        "decision_event_id": evaluation.decision_event_id,
                        "round": evaluation.round,
                        "evidence": list(evaluation.evidence),
                    },
                )
            except MissionMemoryError:
                pass

    def _phase_tasks(
        self,
        mission_id: str,
        round_number: int,
        tasks: tuple[DelegatedTask, ...],
    ) -> tuple[DelegatedTask, ...]:
        existing = {item.task.task_id for item in self.store.list_tasks(mission_id)}
        prefix = f"r{round_number}_"
        planned_ids = {task.task_id for task in tasks}
        mapping = {
            task.task_id: f"{prefix}{task.task_id}"
            for task in tasks
        }
        if len(set(mapping.values())) != len(mapping):
            raise DirectorRuntimeError("Director follow-up task ids collide")
        if existing & set(mapping.values()):
            raise DirectorRuntimeError("Director follow-up task ids already exist")
        result = []
        for task in tasks:
            result.append(
                DelegatedTask(
                    task_id=mapping[task.task_id],
                    role=task.role,
                    objective=task.objective,
                    capability=task.capability,
                    args=task.args,
                    depends_on=tuple(mapping.get(dep, dep) for dep in task.depends_on),
                    skills=task.skills,
                )
            )
        external = {
            dep
            for task in tasks
            for dep in task.depends_on
            if dep not in planned_ids
        }
        existing_ids = {item.task.task_id for item in self.store.list_tasks(mission_id)}
        if external - existing_ids:
            raise DirectorRuntimeError(
                "Director follow-up references unknown prior task(s): "
                + ", ".join(sorted(external - existing_ids))
            )
        return tuple(result)

    def _review_rounds(self, mission_id: str) -> int:
        """Count durable Director review attempts, including failed attempts."""
        return sum(
            1
            for event in self.store.events(mission_id)
            if event.event_type == "director_review_started"
        )

    def _is_finalized(self, mission_id: str) -> bool:
        return any(
            event.event_type in {
                "mission_succeeded",
                "mission_failed",
                "mission_blocked",
            }
            for event in self.store.events(mission_id)
        )

    def _publish_terminal(
        self,
        mission_id: str,
        *,
        status: str,
        reason: str,
    ) -> None:
        if self._is_finalized(mission_id):
            return
        payload = {
            "status": status,
            "reason": reason,
            "tasks": [
                {
                    "task_id": item.task.task_id,
                    "status": item.status,
                    "attempts": item.attempts,
                    "last_error": item.last_error,
                }
                for item in self.store.list_tasks(mission_id)
            ],
        }
        self.event_bus.publish(
            mission_id=mission_id,
            event_type=f"mission_{status}",
            payload=payload,
        )

    def _objective_from_memory(self, mission_id: str) -> str:
        for message in self.memory.list(mission_id, limit=50):
            if message.kind == "mission_objective":
                return message.content
        raise DirectorRuntimeError(f"mission objective missing from memory: {mission_id}")

    def _team_state_text(self, mission_id: str) -> str:
        events = [
            event for event in self.store.events(mission_id)
            if event.event_type in {"team.formed", "team.reconfigured"}
        ]
        if not events:
            return ""
        return "Current specialist team state: " + json.dumps(events[-1].payload, sort_keys=True)

    def _routing_evidence(self, mission_id: str) -> list[dict[str, Any]]:
        """Return the immutable routing snapshots available to the Director review."""
        evidence: list[dict[str, Any]] = []
        for item in self.store.list_tasks(mission_id):
            result = item.result or {}
            routing = result.get("routing")
            if not isinstance(routing, dict):
                continue
            evidence.append(
                {
                    "task_id": item.task.task_id,
                    "status": item.status,
                    "selected_model": routing.get("selected_model"),
                    "score": routing.get("score"),
                    "factors": dict(routing.get("factors") or {}),
                    "candidates": [
                        {
                            "model": candidate.get("model"),
                            "score": candidate.get("score"),
                        }
                        for candidate in (routing.get("candidates") or [])
                        if isinstance(candidate, dict)
                    ],
                }
            )
        return evidence

    def _task_state_text(self, mission_id: str) -> str:
        tasks = self.store.list_tasks(mission_id)
        lines = ["Current durable task state:"]
        for item in tasks:
            detail = item.result or {}
            lines.append(
                f"- {item.task.task_id}: {item.status}; attempts={item.attempts}; "
                f"error={item.last_error or 'none'}; result={detail}"
            )
        evaluations = [
            event for event in self.store.events(mission_id)
            if event.event_type == "skill.evaluated"
        ]
        if evaluations:
            lines.append("Observed specialist evaluations:")
            for event in evaluations:
                lines.append(
                    f"- task={event.task_id}; skill={event.payload.get('skill_id')}; "
                    f"version={event.payload.get('skill_version')}; "
                    f"score={event.payload.get('score')}; "
                    f"evidence_state={event.payload.get('evidence_state')}; "
                    f"model={event.payload.get('model')}"
                )
        return "\n".join(lines)

    @staticmethod
    def _result_text(task_id: str, status: str, result: dict, error: str | None) -> str:
        return (
            f"Task {task_id} status={status}; result={result}; "
            f"error={error or 'none'}"
        )

    @staticmethod
    def _plan_text(tasks: tuple[DelegatedTask, ...]) -> str:
        return "\n".join(
            f"- {task.task_id} [{task.role}] capability={task.capability or 'reasoning'} "
            f"skills={','.join(task.skills) or 'none'} "
            f"depends_on={','.join(task.depends_on) or 'none'}: {task.objective}"
            for task in tasks
        )

    def _results_snapshot(self, mission_id: str) -> tuple[DelegatedTaskResult, ...]:
        results: list[DelegatedTaskResult] = []
        for stored in self.store.list_tasks(mission_id):
            status = self._status_as_task_status(stored.status)
            results.append(
                DelegatedTaskResult(
                    task=stored.task,
                    status=status,
                    error=stored.last_error,
                )
            )
        return tuple(results)

    def _final_status(self, mission_id: str) -> TaskStatus:
        for event in reversed(self.store.events(mission_id)):
            if event.event_type in {"mission_succeeded", "mission_failed", "mission_blocked"}:
                return self._status_as_task_status(event.event_type.removeprefix("mission_"))
        raise DirectorRuntimeError(f"mission has no final event: {mission_id}")

    def _mission_events(self, mission_id: str) -> tuple[TaskEvent, ...]:
        return self.store.events(mission_id)

    @staticmethod
    def _status_as_task_status(status: str) -> TaskStatus:
        try:
            return TaskStatus(status)
        except ValueError as exc:
            raise DirectorRuntimeError(f"unknown durable mission status: {status}") from exc


__all__ = [
    "DirectorRuntime",
    "DirectorRuntimeError",
    "MissionRuntimeResult",
    "RuntimeCycle",
]