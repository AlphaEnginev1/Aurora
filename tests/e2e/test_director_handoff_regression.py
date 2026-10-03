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


class _Orchestration:
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs["task_id"])
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="handoff-regression-model",
                provider="handoff-regression",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("handoff regression proof does not use capability execution")


class _Planner:
    def __init__(self, *, allow_review=True):
        self.plan_calls = 0
        self.review_calls = 0
        self.allow_review = allow_review

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
            model="handoff-regression-director",
        )

    def review(self, **kwargs):
        self.review_calls += 1
        if not self.allow_review:
            raise AssertionError("non-designated successor must not review")
        return DirectorReview(
            decision="complete",
            rationale="Persisted mission state is sufficient.",
            tasks=(),
            model="handoff-regression-director",
        )


class _Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self):
        return self.value


def _runtime(*, director, store, bus, ownership, worker_id, event_consumer_id, clock):
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


def _prepare_handoff(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    orchestration = _Orchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    planner_a = _Planner()
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
        ownership=ownership,
        worker_id="director-A",
        event_consumer_id="director-A-events",
        clock=clock,
    )
    submission = runtime_a.submit_mission(
        objective="Prove cooperative handoff fencing."
    )
    runtime_a.run_once()
    runtime_a.request_director_handoff(
        submission.mission_id,
        "director-B",
        handoff_id="handoff-regression-001",
        now="2026-01-01T00:00:01+00:00",
    )
    return (
        submission,
        store,
        bus,
        ownership,
        clock,
        orchestration,
        engine,
        runtime_a,
    )


def test_predecessor_is_fenced_after_cooperative_handoff(tmp_path: Path):
    submission, store, bus, ownership, clock, orchestration, engine, runtime_a = _prepare_handoff(tmp_path)

    before = len(store.events(submission.mission_id))
    cycle = runtime_a.run_once()
    after_events = store.events(submission.mission_id)

    assert not cycle.worker_cycles
    assert len(after_events) == before
    assert orchestration.calls == ["step_a"]
    assert ownership.get(submission.mission_id, now="2026-01-01T00:00:01+00:00") is None

    # A cannot create a second terminal outcome after release.
    runtime_a._publish_terminal(
        submission.mission_id,
        status="succeeded",
        reason="stale predecessor must be fenced",
    )
    events_after_rejected_mutation = store.events(submission.mission_id)
    assert len(events_after_rejected_mutation) == before + 1
    assert events_after_rejected_mutation[-1].event_type == "director_handoff_mutation_rejected"
    assert not any(
        event.event_type == "mission_succeeded"
        for event in events_after_rejected_mutation
    )


def test_only_designated_successor_can_take_cooperative_handoff(tmp_path: Path):
    submission, store, bus, ownership, clock, orchestration, engine, runtime_a = _prepare_handoff(tmp_path)

    planner_c = _Planner(allow_review=False)
    director_c = DurableDirectorService(
        planner=planner_c,
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    runtime_c = _runtime(
        director=director_c,
        store=store,
        bus=bus,
        ownership=ownership,
        worker_id="director-C",
        event_consumer_id="director-C-events",
        clock=clock,
    )

    cycle = runtime_c.run_once()

    assert not cycle.worker_cycles
    assert planner_c.plan_calls == 0
    assert planner_c.review_calls == 0
    assert ownership.get(submission.mission_id, now="2026-01-01T00:00:01+00:00") is None
    handoff = runtime_a.director_handoff_store.get_active_for_mission(submission.mission_id)
    assert handoff is not None
    assert handoff.state == "RELEASED"
    assert handoff.successor_id == "director-B"

    planner_b = _Planner()
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
        ownership=ownership,
        worker_id="director-B",
        event_consumer_id="director-B-events",
        clock=clock,
    )

    result = runtime_b.run_until_terminal(submission, max_cycles=20)

    assert result.status is TaskStatus.SUCCEEDED
    assert orchestration.calls == ["step_a", "step_b"]
    assert planner_b.plan_calls == 0
    assert planner_b.review_calls == 1
    owner = ownership.get(submission.mission_id)
    assert owner is not None
    assert owner.owner_id == "director-B"
    assert owner.fencing_epoch == 2
    assert sum(
        event.event_type == "mission_succeeded"
        for event in store.events(submission.mission_id)
    ) == 1
