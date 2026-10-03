from datetime import datetime
from pathlib import Path


from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.director_ownership import DirectorOwnershipStore
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy


class _HandoffOrchestration:
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs["task_id"])
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="handoff-test-model",
                provider="handoff-test",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("handoff proof does not use capability execution")


class _DirectorAPlanner:
    def __init__(self):
        self.plan_calls = 0

    def plan(self, **kwargs):
        self.plan_calls += 1
        return DirectorPlan(
            objective=kwargs["objective"],
            tasks=(
                DelegatedTask("step_a", "agent", "complete first step"),
                DelegatedTask(
                    "step_b",
                    "agent",
                    "complete second step",
                    depends_on=("step_a",),
                ),
            ),
            model="director-A",
        )

    def review(self, **kwargs):
        raise AssertionError("Director A must not perform the takeover review")


class _DirectorBPlanner:
    def __init__(self):
        self.plan_calls = 0
        self.review_calls = 0
        self.review_context = None

    def plan(self, **kwargs):
        self.plan_calls += 1
        raise AssertionError("Director B must not re-plan the existing mission")

    def review(self, **kwargs):
        self.review_calls += 1
        self.review_context = kwargs["context"]
        return DirectorReview(
            decision="complete",
            rationale="Persisted mission state is sufficient to complete takeover.",
            tasks=(),
            model="director-B",
        )


class _Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self):
        return self.value


def _runtime(
    *,
    director,
    store,
    bus,
    ownership,
    worker_id,
    event_consumer_id,
    clock,
):
    return DirectorRuntime(
        director=director,
        store=store,
        director_ownership=ownership,
        director_lease_seconds=30,
        ownership_now_fn=clock,
        event_bus=bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=1,
            idle_sleep_seconds=0,
        ),
        worker_id=worker_id,
        event_consumer_id=event_consumer_id,
        worker_concurrency=1,
        max_review_rounds=1,
    )


def test_cooperative_director_handoff_is_integrated_into_runtime(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    orchestration = _HandoffOrchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")

    planner_a = _DirectorAPlanner()
    director_a = DurableDirectorService(
        planner=planner_a,
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    runtime_a = _runtime(
        director=director_a,
        store=store,
        bus=bus,
        worker_id="director-A",
        event_consumer_id="director-A-events",
        clock=clock,
        ownership=ownership,
    )

    submission = runtime_a.submit_mission(
        objective="Prove cooperative Director handoff without repeating completed work."
    )
    runtime_a.run_once()

    step_a = store.get(submission.mission_id, "step_a")
    step_b = store.get(submission.mission_id, "step_b")
    assert step_a is not None and step_a.status == "succeeded"
    assert step_b is not None and step_b.status == "pending"
    assert orchestration.calls == ["step_a"]

    handoff = runtime_a.request_director_handoff(
        submission.mission_id,
        "director-B",
        handoff_id="handoff-runtime-001",
        now="2026-01-01T00:00:01+00:00",
    )
    assert handoff.state == "RELEASED"
    assert handoff.predecessor_epoch == 1
    assert runtime_a._mission_ownership.get(submission.mission_id) is None

    planner_b = _DirectorBPlanner()
    director_b = DurableDirectorService(
        planner=planner_b,
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    clock.value = "2026-01-01T00:00:01+00:00"

    runtime_b = _runtime(
        director=director_b,
        store=store,
        bus=bus,
        worker_id="director-B",
        event_consumer_id="director-B-events",
        clock=clock,
        ownership=ownership,
    )

    result = runtime_b.run_until_terminal(submission, max_cycles=20)

    final_a = store.get(submission.mission_id, "step_a")
    final_b = store.get(submission.mission_id, "step_b")
    owner = ownership.get(submission.mission_id)

    assert result.status is TaskStatus.SUCCEEDED
    assert final_a is not None and final_a.status == "succeeded"
    assert final_b is not None and final_b.status == "succeeded"
    assert orchestration.calls == ["step_a", "step_b"]
    assert planner_a.plan_calls == 1
    assert planner_b.plan_calls == 0
    assert planner_b.review_calls == 1
    assert owner is not None and owner.owner_id == "director-B"
    assert owner.fencing_epoch == 2

    handoff_events = runtime_a.director_handoff_store.events("handoff-runtime-001")
    assert [event.event_type for event in handoff_events] == [
        "HANDOFF_REQUESTED",
        "HANDOFF_QUIESCING",
        "HANDOFF_RELEASED",
        "HANDOFF_SUCCESSOR_ACQUIRED",
        "HANDOFF_COMPLETED",
    ]

    events = store.events(submission.mission_id)
    assert sum(event.event_type == "mission_succeeded" for event in events) == 1
    runtime_handoff_events = [
        event.event_type
        for event in events
        if event.event_type.startswith("director_handoff_")
    ]
    assert runtime_handoff_events == [
        "director_handoff_requested",
        "director_handoff_quiescing",
        "director_handoff_released",
        "director_handoff_acquired",
        "director_handoff_completed",
    ]


def test_director_handoff_reuses_persisted_mission_without_duplicate_execution(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    orchestration = _HandoffOrchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")

    planner_a = _DirectorAPlanner()
    director_a = DurableDirectorService(
        planner=planner_a,
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    runtime_a = _runtime(
        director=director_a,
        store=store,
        bus=bus,
        worker_id="director-A",
        event_consumer_id="director-A-events",
        clock=clock,
        ownership=ownership,
    )

    submission = runtime_a.submit_mission(
        objective="Prove Director handoff without repeating completed work."
    )
    first_cycle = runtime_a.run_once()

    step_a = store.get(submission.mission_id, "step_a")
    step_b = store.get(submission.mission_id, "step_b")
    assert first_cycle.did_work
    assert step_a is not None and step_a.status == "succeeded"
    assert step_b is not None and step_b.status == "pending"
    assert orchestration.calls == ["step_a"]

    planner_b = _DirectorBPlanner()
    director_b = DurableDirectorService(
        planner=planner_b,
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    runtime_b = _runtime(
        director=director_b,
        store=store,
        bus=bus,
        worker_id="director-B",
        event_consumer_id="director-B-events",
        clock=clock,
        ownership=ownership,
    )

    # Director A's lease was renewed during its last cycle. Simulate A going
    # offline by advancing the deterministic clock past the 30-second lease.
    clock.value = "2026-01-01T00:00:31+00:00"

    result = runtime_b.run_until_terminal(submission, max_cycles=20)

    final_a = store.get(submission.mission_id, "step_a")
    final_b = store.get(submission.mission_id, "step_b")

    assert result.status is TaskStatus.SUCCEEDED
    assert final_a is not None and final_a.status == "succeeded"
    assert final_b is not None and final_b.status == "succeeded"

    assert orchestration.calls == ["step_a", "step_b"]
    assert planner_a.plan_calls == 1
    assert planner_b.plan_calls == 0
    assert planner_b.review_calls == 1
    assert planner_b.review_context is not None
    assert any(
        "Prove Director handoff without repeating completed work."
        in message.get("content", "")
        for message in planner_b.review_context
    )

    events = store.events(submission.mission_id)
    assert sum(event.event_type == "mission_succeeded" for event in events) == 1
