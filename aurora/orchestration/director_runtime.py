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
        self._sleep = sleep_fn
        self._subscription: EventSubscription = self.event_bus.subscribe(event_consumer_id)
        self._workers: dict[str, TaskWorker] = {}


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

        self.event_bus.publish(
            mission_id=mission_id,
            event_type="director_review",
            payload={
                "round": round_number,
                "decision": review.decision,
                "rationale": review.rationale,
                "model": review.model,
            },
        )
        try:
            self.memory.append(
                mission_id=mission_id,
                source="director",
                kind="review",
                content=f"Decision={review.decision}; rationale={review.rationale}",
                metadata={"round": round_number, "model": review.model},
            )
        except MissionMemoryError:
            pass

        if review.decision == "complete":
            self._publish_terminal(
                mission_id,
                status="succeeded",
                reason=review.rationale,
            )
            return
        if review.decision == "blocked":
            self._publish_terminal(
                mission_id,
                status="blocked",
                reason=review.rationale,
            )
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

    def _task_state_text(self, mission_id: str) -> str:
        tasks = self.store.list_tasks(mission_id)
        lines = ["Current durable task state:"]
        for item in tasks:
            detail = item.result or {}
            lines.append(
                f"- {item.task.task_id}: {item.status}; attempts={item.attempts}; "
                f"error={item.last_error or 'none'}; result={detail}"
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
