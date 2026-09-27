"""Adaptive 24/7 Director runtime with durable memory and review/replan cycles."""

from __future__ import annotations

from dataclasses import dataclass
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
        max_review_rounds: int = 3,
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
        self.director = director
        self.store = store
        self.event_bus = event_bus or DurableEventBus(store)
        self.memory = memory or MissionMemory(store)
        self.worker_policy = worker_policy or WorkerPolicy()
        self.worker_id = worker_id or f"director-worker-{uuid4().hex[:12]}"
        self.event_batch_size = event_batch_size
        self.idle_sleep_seconds = idle_sleep_seconds
        self.max_review_rounds = max_review_rounds
        self._sleep = sleep_fn
        self._subscription: EventSubscription = self.event_bus.subscribe(event_consumer_id)
        self._workers: dict[str, TaskWorker] = {}

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

        cycles: list[WorkerCycle] = []
        for mission_id in active:
            if not self.store.has_event(mission_id, "mission_started"):
                self.event_bus.publish(
                    mission_id=mission_id,
                    event_type="mission_started",
                    payload={"worker_id": self.worker_id},
                )
            worker = self._worker_for(mission_id)
            cycle = worker.run_once()
            cycles.append(cycle)
            self._record_worker_result(mission_id, cycle)
            if self.store.mission_status(mission_id) in self.TERMINAL:
                terminal_candidates.append(mission_id)

        for mission_id in sorted(set(terminal_candidates)):
            self._review_or_finalize(mission_id)

        for event in events:
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

    def _worker_for(self, mission_id: str) -> TaskWorker:
        worker = self._workers.get(mission_id)
        if worker is None:
            worker = TaskWorker(
                store=self.store,
                task_engine=self.director.task_engine,
                mission_id=mission_id,
                worker_id=f"{self.worker_id}:{mission_id}",
                policy=self.worker_policy,
                event_bus=self.event_bus,
            )
            self._workers[mission_id] = worker
        return worker

    def _record_worker_result(self, mission_id: str, cycle: WorkerCycle) -> None:
        if not cycle.task_id or cycle.action not in {"succeeded", "failed", "retried"}:
            return
        stored = self.store.get(mission_id, cycle.task_id)
        if stored is None:
            return
        result = stored.result or {}
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
            validated = self.director.task_engine.validate(tasks)
            self.store.enqueue(mission_id, validated)
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
        return sum(
            1
            for event in self.store.events(mission_id)
            if event.event_type == "director_review"
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
