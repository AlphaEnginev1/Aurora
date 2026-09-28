"""Durable task worker for continuous mission execution."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from time import sleep
from typing import Callable

from .event_bus import DurableEventBus, EventBusError
from .task_engine import DelegatedTaskResult, ResearchTaskResult, TaskEngine, TaskStatus
from .task_store import DurableTaskStore, StoredTask, TaskStoreError
from aurora.skills.evaluation import SkillEvaluator
from aurora.skills.learning import SkillEvolutionEngine, SkillLearningStore


class TaskWorkerError(RuntimeError):
    """Raised when worker configuration or durable ownership is invalid."""


@dataclass(frozen=True)
class WorkerPolicy:
    lease_seconds: float = 300.0
    max_attempts: int = 3
    idle_sleep_seconds: float = 0.5
    heartbeat_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.lease_seconds <= 0:
            raise TaskWorkerError("lease_seconds must be positive")
        if self.max_attempts < 1:
            raise TaskWorkerError("max_attempts must be at least 1")
        if self.idle_sleep_seconds < 0:
            raise TaskWorkerError("idle_sleep_seconds cannot be negative")
        heartbeat = self.heartbeat_seconds
        if heartbeat is not None and heartbeat <= 0:
            raise TaskWorkerError("heartbeat_seconds must be positive")
        if heartbeat is not None and heartbeat >= self.lease_seconds:
            raise TaskWorkerError("heartbeat_seconds must be shorter than lease_seconds")

    @property
    def effective_heartbeat_seconds(self) -> float:
        return self.heartbeat_seconds or max(self.lease_seconds / 3.0, 0.1)


@dataclass(frozen=True)
class WorkerCycle:
    action: str
    task_id: str | None = None
    task_status: TaskStatus | None = None
    recovered_tasks: int = 0
    blocked_tasks: int = 0


class TaskWorker:
    """Continuously claim, execute, recover, and retry durable tasks."""

    def __init__(
        self,
        *,
        store: DurableTaskStore,
        task_engine: TaskEngine,
        mission_id: str,
        worker_id: str,
        policy: WorkerPolicy | None = None,
        event_bus: DurableEventBus | None = None,
        skill_evaluator: SkillEvaluator | None = None,
        skill_learning: SkillLearningStore | None = None,
        skill_evolution: SkillEvolutionEngine | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        if not mission_id.strip():
            raise TaskWorkerError("mission_id is empty")
        if not worker_id.strip():
            raise TaskWorkerError("worker_id is empty")
        self.store = store
        self.task_engine = task_engine
        self.mission_id = mission_id
        self.worker_id = worker_id
        self.policy = policy or WorkerPolicy()
        self.event_bus = event_bus
        self.skill_evaluator = skill_evaluator
        self.skill_learning = skill_learning
        self.skill_evolution = skill_evolution
        self._sleep = sleep_fn

    def run_once(self) -> WorkerCycle:
        recovered = self.store.recover_expired(self.mission_id)
        blocked = self.store.block_dependents(self.mission_id)
        claimed = self.store.claim_next(
            self.mission_id,
            self.worker_id,
            lease_seconds=self.policy.lease_seconds,
        )
        if claimed is None:
            return WorkerCycle(
                action="idle",
                recovered_tasks=recovered,
                blocked_tasks=blocked,
            )

        result = self._execute_claimed(claimed)
        if result.status is TaskStatus.SUCCEEDED:
            result_payload = self._result_payload(result)
            self.store.complete(
                self.mission_id,
                claimed.task.task_id,
                status="succeeded",
                payload={
                    "attempt": claimed.attempts,
                    "result_type": self._result_type(result),
                },
                result=result_payload,
                worker_id=self.worker_id,
                claim_token=claimed.claim_token,
            )
            self._emit(
                "worker.task_succeeded",
                claimed.task.task_id,
                {
                    "worker_id": self.worker_id,
                    "attempt": claimed.attempts,
                },
            )
            return WorkerCycle(
                action="succeeded",
                task_id=claimed.task.task_id,
                task_status=TaskStatus.SUCCEEDED,
                recovered_tasks=recovered,
                blocked_tasks=blocked,
            )

        error = result.error or "task execution failed"
        if claimed.attempts < self.policy.max_attempts:
            self.store.retry(
                self.mission_id,
                claimed.task.task_id,
                worker_id=self.worker_id,
                error=error,
                claim_token=claimed.claim_token,
            )
            self._emit(
                "worker.task_retried",
                claimed.task.task_id,
                {
                    "worker_id": self.worker_id,
                    "attempt": claimed.attempts,
                    "error": error,
                },
            )
            return WorkerCycle(
                action="retried",
                task_id=claimed.task.task_id,
                task_status=TaskStatus.FAILED,
                recovered_tasks=recovered,
                blocked_tasks=blocked,
            )

        self.store.complete(
            self.mission_id,
            claimed.task.task_id,
            status="failed",
            error=error,
            payload={"attempt": claimed.attempts},
            result={"error": error, "attempt": claimed.attempts},
            worker_id=self.worker_id,
            claim_token=claimed.claim_token,
        )
        newly_blocked = self.store.block_dependents(self.mission_id)
        self._emit(
            "worker.task_failed",
            claimed.task.task_id,
            {
                "worker_id": self.worker_id,
                "attempt": claimed.attempts,
                "blocked_tasks": newly_blocked,
                "error": error,
            },
        )
        return WorkerCycle(
            action="failed",
            task_id=claimed.task.task_id,
            task_status=TaskStatus.FAILED,
            recovered_tasks=recovered,
            blocked_tasks=blocked + newly_blocked,
        )

    def run_until_idle(self, *, max_cycles: int = 1000) -> tuple[WorkerCycle, ...]:
        if max_cycles < 1:
            raise TaskWorkerError("max_cycles must be at least 1")
        cycles: list[WorkerCycle] = []
        for _ in range(max_cycles):
            cycle = self.run_once()
            cycles.append(cycle)
            if cycle.action == "idle":
                return tuple(cycles)
        raise TaskWorkerError("worker did not become idle within max_cycles")

    def run_forever(self, stop_event: Event | None = None) -> None:
        stop = stop_event or Event()
        while not stop.is_set():
            cycle = self.run_once()
            if cycle.action == "idle" and self.policy.idle_sleep_seconds:
                self._sleep(self.policy.idle_sleep_seconds)

    def _execute_claimed(self, claimed: StoredTask) -> DelegatedTaskResult:
        heartbeat_stop = Event()
        heartbeat_errors: list[Exception] = []

        def heartbeat() -> None:
            while not heartbeat_stop.wait(self.policy.effective_heartbeat_seconds):
                try:
                    self.store.renew_lease(
                        self.mission_id,
                        claimed.task.task_id,
                        self.worker_id,
                        claim_token=claimed.claim_token,
                        lease_seconds=self.policy.lease_seconds,
                    )
                except TaskStoreError as exc:
                    heartbeat_errors.append(exc)
                    return

        thread = Thread(target=heartbeat, name=f"aurora-heartbeat-{claimed.task.task_id}", daemon=True)
        thread.start()
        try:
            result = self.task_engine.execute_one(
                claimed.task,
                execution_scope=(
                    f"{self.mission_id}:{claimed.task.task_id}:attempt:{claimed.attempts}"
                ),
            )
        except Exception as exc:
            result = DelegatedTaskResult(
                claimed.task,
                TaskStatus.FAILED,
                error=str(exc),
            )
        finally:
            heartbeat_stop.set()
            thread.join(timeout=self.policy.effective_heartbeat_seconds + 0.1)

        if heartbeat_errors:
            raise TaskWorkerError(
                f"lease renewal failed for {claimed.task.task_id}: {heartbeat_errors[0]}"
            )
        self._learn_from_result(claimed, result)
        return result

    def _learn_from_result(self, claimed: StoredTask, result: DelegatedTaskResult) -> None:
        if self.skill_evaluator is None or self.skill_learning is None:
            return
        organization = self.task_engine.organization
        registry = self.task_engine.skill_registry
        if organization is None or registry is None:
            return
        try:
            agent = organization.resolve_agent(claimed.task.role)
            evaluations = self.skill_evaluator.evaluate(
                task=claimed.task,
                result=result,
                mission_id=self.mission_id,
                default_skill_ids=agent.skill_ids,
                agent_capabilities=agent.capabilities,
            )
            for evaluation in evaluations:
                self.skill_learning.record(evaluation)
                self._emit(
                    "skill.evaluated",
                    claimed.task.task_id,
                    {
                        "skill_id": evaluation.skill_id,
                        "skill_version": evaluation.skill_version,
                        "score": evaluation.score,
                        "status": evaluation.status,
                        "model": evaluation.model,
                        "verification_passed": list(evaluation.verification_passed),
                        "verification_failed": list(evaluation.verification_failed),
                        "verification_missing": list(evaluation.verification_missing),
                        "quality_passed": list(evaluation.quality_passed),
                        "quality_failed": list(evaluation.quality_failed),
                        "quality_missing": list(evaluation.quality_missing),
                    },
                )
                if self.skill_evolution is not None:
                    proposal = self.skill_evolution.observe(
                        evaluation,
                        base_version=evaluation.skill_version,
                    )
                    if proposal is not None:
                        self._emit(
                            "skill.evolution_proposed",
                            claimed.task.task_id,
                            {
                                "proposal_id": proposal.proposal_id,
                                "skill_id": proposal.skill_id,
                                "base_version": proposal.base_version,
                                "candidate_version": proposal.candidate_version,
                                "reason": proposal.reason,
                                "suggested_change": proposal.suggested_change,
                            },
                        )
        except Exception as exc:
            self._emit(
                "skill.learning_failed",
                claimed.task.task_id,
                {"error": str(exc)},
            )

    def _emit(
        self,
        event_type: str,
        task_id: str,
        payload: dict,
    ) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(
                mission_id=self.mission_id,
                task_id=task_id,
                event_type=event_type,
                payload=payload,
            )
        except EventBusError:
            # Task state is authoritative in DurableTaskStore. Observability
            # events must never turn a committed task transition into a retry.
            return

    @staticmethod
    def _result_payload(result: DelegatedTaskResult) -> dict:
        """Persist a bounded, model-readable task result."""
        limit = 16_384
        if result.model_result is not None:
            content = result.model_result.response.content
            return {
                "type": "model",
                "model": result.model_result.response.model,
                "content": content[:limit],
                "truncated": len(content) > limit,
            }
        capability = result.capability_result
        if isinstance(capability, ResearchTaskResult):
            return {
                "type": "research",
                "request_id": capability.request_id,
                "source_ids": list(capability.source_ids),
                "claim_ids": list(capability.claim_ids),
            }
        if capability is not None:
            execution = getattr(capability, "execution", None)
            if execution is not None:
                stdout = str(getattr(execution, "stdout", ""))[:limit]
                stderr = str(getattr(execution, "stderr", ""))[:limit]
                return {
                    "type": "capability",
                    "returncode": getattr(execution, "returncode", None),
                    "stdout": stdout,
                    "stderr": stderr,
                    "truncated": (
                        len(str(getattr(execution, "stdout", ""))) > limit
                        or len(str(getattr(execution, "stderr", ""))) > limit
                    ),
                }
        return {"type": "none"}

    @staticmethod
    def _result_type(result: DelegatedTaskResult) -> str:
        if result.model_result is not None:
            return "model"
        if result.capability_result is not None:
            return "capability"
        return "none"


__all__ = ["TaskWorker", "TaskWorkerError", "WorkerCycle", "WorkerPolicy"]
