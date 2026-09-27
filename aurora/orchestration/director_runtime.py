"""24/7 Director runtime connecting durable missions, Event Bus, and workers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from time import sleep
from typing import Any, Callable
from uuid import uuid4

from .director import DirectorPlan
from .durable_director import DurableDirectorService, MissionSubmission
from .event_bus import DurableEventBus, EventSubscription
from .task_engine import DelegatedTaskResult, TaskStatus
from .task_store import DurableTaskStore, TaskEvent
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
        return bool(self.worker_cycles) or bool(self.events)


@dataclass(frozen=True)
class MissionRuntimeResult:
    submission: MissionSubmission
    status: TaskStatus
    tasks: tuple[DelegatedTaskResult, ...]
    events: tuple[TaskEvent, ...]


class DirectorRuntime:
    """Own the continuous Director -> durable queue -> Event Bus -> worker loop."""

    TERMINAL = frozenset({"succeeded", "failed", "blocked"})

    def __init__(
        self,
        *,
        director: DurableDirectorService,
        store: DurableTaskStore,
        event_bus: DurableEventBus | None = None,
        worker_policy: WorkerPolicy | None = None,
        worker_id: str | None = None,
        event_consumer_id: str = "aurora-director-runtime",
        event_batch_size: int = 100,
        idle_sleep_seconds: float = 0.5,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        if not event_consumer_id.strip():
            raise DirectorRuntimeError("event_consumer_id is empty")
        if event_batch_size < 1:
            raise DirectorRuntimeError("event_batch_size must be at least 1")
        if idle_sleep_seconds < 0:
            raise DirectorRuntimeError("idle_sleep_seconds cannot be negative")
        self.director = director
        self.store = store
        self.event_bus = event_bus or DurableEventBus(store)
        self.worker_policy = worker_policy or WorkerPolicy()
        self.worker_id = worker_id or f"director-worker-{uuid4().hex[:12]}"
        self.event_batch_size = event_batch_size
        self.idle_sleep_seconds = idle_sleep_seconds
        self._sleep = sleep_fn
        self._subscription: EventSubscription = self.event_bus.subscribe(event_consumer_id)
        self._workers: dict[str, TaskWorker] = {}

    def submit_mission(self, **kwargs: Any) -> MissionSubmission:
        """Plan and durably submit a mission for the continuous worker loop."""
        return self.director.submit_mission(**kwargs)

    def run_once(self) -> RuntimeCycle:
        """Consume new events, advance all active missions, and emit lifecycle results."""
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

        cycles: list[WorkerCycle] = []
        for mission_id in active:
            if not self.store.has_event(mission_id, "mission_started"):
                self.event_bus.publish(
                    mission_id=mission_id,
                    event_type="mission_started",
                    payload={"worker_id": self.worker_id},
                )
            worker = self._worker_for(mission_id)
            cycles.append(worker.run_once())
            self._finalize_mission(mission_id)

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
            status = self._status_as_task_status(
                self.store.mission_status(submission.mission_id)
            )
            if status in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.BLOCKED,
            }:
                return MissionRuntimeResult(
                    submission=submission,
                    status=status,
                    tasks=self._results_snapshot(submission.mission_id),
                    events=tuple(observed) + self._mission_events(submission.mission_id),
                )
        raise DirectorRuntimeError(
            f"mission did not reach a terminal state within max_cycles: {submission.mission_id}"
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

    def _finalize_mission(self, mission_id: str) -> None:
        status = self.store.mission_status(mission_id)
        if status not in self.TERMINAL:
            return
        event_type = f"mission_{status}"
        if self.store.has_event(mission_id, event_type):
            return
        tasks = self.store.list_tasks(mission_id)
        self.event_bus.publish(
            mission_id=mission_id,
            event_type=event_type,
            payload={
                "status": status,
                "tasks": [
                    {
                        "task_id": item.task.task_id,
                        "status": item.status,
                        "attempts": item.attempts,
                        "last_error": item.last_error,
                    }
                    for item in tasks
                ],
            },
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
