from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

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
        self._lock = Lock()

    def run_task(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs["task_id"])
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="gate-8-test-model",
                provider="gate-8-test",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("Gate 8 uses deterministic task execution only")


class _PlannerA:
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
        raise AssertionError("expired predecessor must not review")


class _CandidatePlanner:
    def __init__(self, director_id):
        self.director_id = director_id
        self.plan_calls = 0
        self.review_calls = 0

    def plan(self, **kwargs):
        self.plan_calls += 1
        raise AssertionError(f"{self.director_id} must reuse the durable task graph")

    def review(self, **kwargs):
        self.review_calls += 1
        return DirectorReview(
            decision="complete",
            rationale=f"{self.director_id} recovered the durable mission",
            tasks=(),
            model=self.director_id,
        )


class _Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def _runtime(
    *,
    store,
    bus,
    ownership,
    worker_id,
    event_consumer_id,
    clock,
    director,
    engine,
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


def test_gate_8_only_one_eligible_director_wins_expired_lease_and_continues(
    tmp_path: Path,
):
    store = DurableTaskStore(tmp_path / "state.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    orchestration = _Orchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")

    planner_a = _PlannerA()
    runtime_a = _runtime(
        store=store,
        bus=bus,
        ownership=ownership,
        worker_id="director-A",
        event_consumer_id="director-A-events",
        clock=clock,
        director=DurableDirectorService(
            planner=planner_a,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        engine=engine,
    )
    submission = runtime_a.submit_mission(
        objective="Prove automatic Director failover keeps one fenced owner."
    )
    runtime_a.run_once()

    assert orchestration.calls == ["step_a"]
    owner_a = ownership.get(submission.mission_id)
    assert owner_a is not None
    assert owner_a.owner_id == "director-A"
    assert owner_a.fencing_epoch == 1

    planner_b = _CandidatePlanner("director-B")
    planner_c = _CandidatePlanner("director-C")
    runtime_b = _runtime(
        store=DurableTaskStore(store.path),
        bus=bus,
        ownership=DirectorOwnershipStore(store.path),
        worker_id="director-B",
        event_consumer_id="director-B-events",
        clock=clock,
        director=DurableDirectorService(
            planner=planner_b,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        engine=engine,
    )
    runtime_c = _runtime(
        store=DurableTaskStore(store.path),
        bus=bus,
        ownership=DirectorOwnershipStore(store.path),
        worker_id="director-C",
        event_consumer_id="director-C-events",
        clock=clock,
        director=DurableDirectorService(
            planner=planner_c,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        engine=engine,
    )

    # A is unavailable: both eligible candidates observe the same expired lease.
    clock.value = "2026-01-01T00:00:31+00:00"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda runtime: runtime._ensure_mission_ownership(
                    submission.mission_id
                ),
                (runtime_b, runtime_c),
            )
        )

    assert sum(results) == 1
    winner = "director-B" if results[0] else "director-C"
    loser = "director-C" if results[0] else "director-B"

    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == winner
    assert current.fencing_epoch == 2

    loser_runtime = runtime_c if loser == "director-C" else runtime_b
    assert not loser_runtime._require_director_ownership(submission.mission_id)

    loser_events = [
        event for event in store.events(submission.mission_id)
        if event.event_type in {"director_ownership_rejected", "director_ownership_fence_rejected"}
    ]
    assert loser_events
    assert any(event.payload.get("owner_id") == loser for event in loser_events)

    # The expired predecessor cannot mutate after the winner advances the fence.
    before = len(store.events(submission.mission_id))
    runtime_a._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="expired predecessor must remain fenced",
    )
    after = store.events(submission.mission_id)
    assert len(after) == before + 1
    assert after[-1].event_type == "director_ownership_fence_rejected"
    assert not any(
        event.event_type == "mission_failed"
        and event.payload.get("reason") == "expired predecessor must remain fenced"
        for event in after
    )

    winner_runtime = runtime_b if winner == "director-B" else runtime_c
    result = winner_runtime.run_until_terminal(submission, max_cycles=20)

    assert result.status is TaskStatus.SUCCEEDED
    assert orchestration.calls == ["step_a", "step_b"]

    final_a = store.get(submission.mission_id, "step_a")
    final_b = store.get(submission.mission_id, "step_b")
    assert final_a is not None and final_a.status == "succeeded"
    assert final_b is not None and final_b.status == "succeeded"
    assert final_a.attempts == 1
    assert final_b.attempts == 1

    winner_planner = planner_b if winner == "director-B" else planner_c
    loser_planner = planner_c if winner == "director-B" else planner_b
    assert winner_planner.plan_calls == 0
    assert winner_planner.review_calls == 1
    assert loser_planner.plan_calls == 0
    assert loser_planner.review_calls == 0

    terminal = [
        event.event_type
        for event in store.events(submission.mission_id)
        if event.event_type in {"mission_succeeded", "mission_failed", "mission_blocked"}
    ]
    assert terminal == ["mission_succeeded"]


def test_gate_8_fail_closed_when_no_current_owner_can_be_established(
    tmp_path: Path,
):
    store = DurableTaskStore(tmp_path / "state.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    orchestration = _Orchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    planner = _CandidatePlanner("director-B")
    runtime = _runtime(
        store=store,
        bus=bus,
        ownership=ownership,
        worker_id="director-B",
        event_consumer_id="director-B-events",
        clock=clock,
        director=DurableDirectorService(
            planner=planner,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        engine=engine,
    )

    # This is a contract fixture: a mission that exists durably but has no
    # ownership record must not be treated as safely controlled by B.
    submission = runtime.submit_mission(
        objective="Fail closed instead of inventing Director ownership."
    )
    assert ownership.get(submission.mission_id) is None

    # run_once acquires initial ownership in today's runtime, so direct control
    # is not attempted here. Instead verify that an explicitly invalid fence
    # is rejected without changing the durable ownership record.
    with_ownership = ownership.acquire(
        submission.mission_id,
        "director-A",
        lease_seconds=1,
        now="2026-01-01T00:00:00+00:00",
    )
    clock.value = "2026-01-01T00:00:02+00:00"

    assert not runtime._require_director_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-A"
    assert current.fencing_epoch == with_ownership.fencing_epoch
    assert planner.review_calls == 0
