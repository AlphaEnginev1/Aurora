from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.director_eligibility import (
    DirectorEligibilityStore,
    DirectorEligibilityUnavailable,
)
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
        self.calls: list[str] = []
        self._lock = Lock()

    def run_task(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs["task_id"])
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="gate-8-block-3-test-model",
                provider="gate-8-test",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("Gate 8 Block 3 uses deterministic task execution only")


class _PlannerA:
    def plan(self, **kwargs):
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
    def __init__(self, director_id: str):
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
    def __init__(self, value: str):
        self.value = value

    def __call__(self):
        return self.value


def _register(
    store: DirectorEligibilityStore,
    director_id: str,
    *,
    state: str = DirectorEligibilityStore.ELIGIBLE,
    last_seen_at: str = "2026-01-01T00:00:00+00:00",
    freshness_seconds: float = 60,
    generation: int | None = None,
):
    return store.register(
        director_id,
        state=state,
        ready=True,
        required_capabilities=(
            "durable_mission_access",
            "durable_ownership_access",
        ),
        registration_at="2026-01-01T00:00:00+00:00",
        updated_at=last_seen_at,
        last_seen_at=last_seen_at,
        freshness_seconds=freshness_seconds,
        generation=generation,
    )


def _runtime(
    *,
    store: DurableTaskStore,
    bus: DurableEventBus,
    ownership: DirectorOwnershipStore,
    eligibility: DirectorEligibilityStore,
    worker_id: str,
    event_consumer_id: str,
    clock: _Clock,
    director,
    engine: TaskEngine,
):
    return DirectorRuntime(
        director=director,
        store=store,
        director_ownership=ownership,
        director_eligibility=eligibility,
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


def _build_state(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "state.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    eligibility = DirectorEligibilityStore(store.path)
    orchestration = _Orchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    clock = _Clock("2026-01-01T00:00:00+00:00")
    runtime_a = _runtime(
        store=store,
        bus=bus,
        ownership=ownership,
        eligibility=eligibility,
        worker_id="director-A",
        event_consumer_id="director-A-events",
        clock=clock,
        director=DurableDirectorService(
            planner=_PlannerA(),
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        engine=engine,
    )
    submission = runtime_a.submit_mission(
        objective="prove full automatic Director failover acceptance"
    )
    runtime_a.run_once()
    return store, bus, ownership, eligibility, orchestration, engine, clock, runtime_a, submission


def _candidate(
    *,
    store: DurableTaskStore,
    bus: DurableEventBus,
    ownership: DirectorOwnershipStore,
    eligibility: DirectorEligibilityStore,
    clock: _Clock,
    engine: TaskEngine,
    director_id: str,
    event_consumer_id: str,
):
    planner = _CandidatePlanner(director_id)
    runtime = _runtime(
        store=DurableTaskStore(store.path),
        bus=bus,
        ownership=DirectorOwnershipStore(store.path),
        eligibility=DirectorEligibilityStore(store.path),
        worker_id=director_id,
        event_consumer_id=event_consumer_id,
        clock=clock,
        director=DurableDirectorService(
            planner=planner,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        engine=engine,
    )
    return runtime, planner


def test_gate_8_block_3_full_failover_uses_eligibility_and_preserves_graph(tmp_path: Path):
    (
        store,
        bus,
        ownership,
        eligibility,
        orchestration,
        engine,
        clock,
        runtime_a,
        submission,
    ) = _build_state(tmp_path)

    _register(eligibility, "director-B", last_seen_at="2026-01-01T00:00:30+00:00")
    _register(eligibility, "director-C", last_seen_at="2026-01-01T00:00:30+00:00")
    clock.value = "2026-01-01T00:00:31+00:00"

    runtime_b, planner_b = _candidate(
        store=store,
        bus=bus,
        ownership=ownership,
        eligibility=eligibility,
        clock=clock,
        engine=engine,
        director_id="director-B",
        event_consumer_id="director-B-events",
    )
    runtime_c, planner_c = _candidate(
        store=store,
        bus=bus,
        ownership=ownership,
        eligibility=eligibility,
        clock=clock,
        engine=engine,
        director_id="director-C",
        event_consumer_id="director-C-events",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda runtime: runtime._ensure_mission_ownership(submission.mission_id),
                (runtime_b, runtime_c),
            )
        )

    assert sum(results) == 1
    winner = "director-B" if results[0] else "director-C"
    loser = "director-C" if winner == "director-B" else "director-B"

    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == winner
    assert current.fencing_epoch == 2

    takeover = [
        event
        for event in store.events(submission.mission_id)
        if event.event_type == "director_ownership_takeover"
    ]
    assert len(takeover) == 1
    correlation_id = takeover[0].correlation_id
    assert correlation_id == (
        f"director-failover:{submission.mission_id}:epoch-2"
    )

    loser_events = [
        event
        for event in store.events(submission.mission_id)
        if event.event_type in {
            "director_eligibility_rejected",
            "director_ownership_rejected",
            "director_ownership_fence_rejected",
        }
        and event.payload.get("owner_id", event.payload.get("director_id")) == loser
    ]
    assert loser_events
    assert any(event.correlation_id == correlation_id for event in loser_events)

    before = len(store.events(submission.mission_id))
    runtime_a._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="expired predecessor must remain fenced",
    )
    after = store.events(submission.mission_id)
    assert len(after) == before + 1
    assert after[-1].event_type == "director_ownership_fence_rejected"
    assert after[-1].correlation_id == correlation_id

    winner_runtime = runtime_b if winner == "director-B" else runtime_c
    result = winner_runtime.run_until_terminal(submission, max_cycles=20)
    assert result.status is TaskStatus.SUCCEEDED
    assert orchestration.calls == ["step_a", "step_b"]

    restarted = _candidate(
        store=store,
        bus=bus,
        ownership=ownership,
        eligibility=eligibility,
        clock=clock,
        engine=engine,
        director_id=winner,
        event_consumer_id=f"{winner}-restart-events",
    )[0]
    assert restarted._ensure_mission_ownership(submission.mission_id)
    restarted_owner = ownership.get(submission.mission_id)
    assert restarted_owner is not None
    assert restarted_owner.owner_id == winner
    assert restarted_owner.fencing_epoch == 2

    stale_predecessor = _candidate(
        store=store,
        bus=bus,
        ownership=ownership,
        eligibility=eligibility,
        clock=clock,
        engine=engine,
        director_id="director-A",
        event_consumer_id="director-A-restart-events",
    )[0]
    events_before_stale = len(store.events(submission.mission_id))
    stale_predecessor._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="stale predecessor restart must remain fenced",
    )
    stale_events = store.events(submission.mission_id)
    assert len(stale_events) == events_before_stale + 1
    assert stale_events[-1].event_type == "director_ownership_fence_rejected"
    assert stale_events[-1].correlation_id == correlation_id

    assert store.get(submission.mission_id, "step_a").attempts == 1
    assert store.get(submission.mission_id, "step_b").attempts == 1
    assert planner_b.plan_calls == 0
    assert planner_c.plan_calls == 0

    terminal = [
        event.event_type
        for event in store.events(submission.mission_id)
        if event.event_type in {
            "mission_succeeded",
            "mission_failed",
            "mission_blocked",
        }
    ]
    assert terminal == ["mission_succeeded"]


def test_gate_8_block_3_ineligible_candidate_is_filtered_before_takeover(tmp_path: Path):
    store, bus, ownership, eligibility, _, engine, clock, _, submission = _build_state(tmp_path)
    _register(eligibility, "director-B", state=DirectorEligibilityStore.INELIGIBLE)
    _register(eligibility, "director-C")
    clock.value = "2026-01-01T00:00:31+00:00"

    runtime_b, _ = _candidate(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        clock=clock, engine=engine, director_id="director-B",
        event_consumer_id="director-B-events",
    )
    runtime_c, _ = _candidate(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        clock=clock, engine=engine, director_id="director-C",
        event_consumer_id="director-C-events",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda runtime: runtime._ensure_mission_ownership(submission.mission_id),
                (runtime_b, runtime_c),
            )
        )

    assert results == [False, True]
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-C"
    assert current.fencing_epoch == 2
    rejected = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_eligibility_rejected"
        and event.payload.get("director_id") == "director-B"
    ]
    assert rejected
    assert rejected[-1].payload["reason"] == "explicit_state_ineligible"
    assert rejected[-1].correlation_id == f"director-failover:{submission.mission_id}:epoch-2"


def test_gate_8_block_3_stale_candidate_cannot_take_over(tmp_path: Path):
    store, bus, ownership, eligibility, _, engine, clock, _, submission = _build_state(tmp_path)
    _register(eligibility, "director-B", last_seen_at="2026-01-01T00:00:00+00:00", freshness_seconds=10)
    clock.value = "2026-01-01T00:00:31+00:00"

    candidate, _ = _candidate(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        clock=clock, engine=engine, director_id="director-B",
        event_consumer_id="director-B-events",
    )
    assert not candidate._ensure_mission_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-A"
    assert current.fencing_epoch == 1
    rejected = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_eligibility_rejected"
        and event.payload.get("director_id") == "director-B"
    ]
    assert rejected[-1].payload["reason"] == "eligibility_record_stale"
    assert rejected[-1].correlation_id == f"director-failover:{submission.mission_id}:epoch-2"


@pytest.mark.parametrize("mode", ["unavailable", "contradictory"])
def test_gate_8_block_3_failover_fails_closed_on_eligibility_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    store, bus, ownership, eligibility, _, engine, clock, _, submission = _build_state(tmp_path)
    _register(eligibility, "director-B")
    clock.value = "2026-01-01T00:00:31+00:00"

    candidate, _ = _candidate(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        clock=clock, engine=engine, director_id="director-B",
        event_consumer_id="director-B-events",
    )

    if mode == "unavailable":
        def unavailable(*args, **kwargs):
            raise DirectorEligibilityUnavailable("eligibility authority unavailable")
        monkeypatch.setattr(candidate.director_eligibility, "evaluate", unavailable)
    else:
        _register(
            eligibility,
            "director-B",
            generation=1,
            last_seen_at="2026-01-01T00:00:31+00:00",
        )

    assert not candidate._ensure_mission_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-A"
    assert current.fencing_epoch == 1
    rejected = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_eligibility_rejected"
        and event.payload.get("director_id") == "director-B"
    ]
    assert rejected
    assert rejected[-1].correlation_id == f"director-failover:{submission.mission_id}:epoch-2"


def test_gate_8_block_3_no_record_cannot_enter_failover_candidate_set(tmp_path: Path):
    store, bus, ownership, eligibility, _, engine, clock, _, submission = _build_state(tmp_path)
    clock.value = "2026-01-01T00:00:31+00:00"

    candidate, _ = _candidate(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        clock=clock, engine=engine, director_id="director-B",
        event_consumer_id="director-B-events",
    )
    assert not candidate._ensure_mission_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-A"
    assert current.fencing_epoch == 1
    rejected = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_eligibility_rejected"
        and event.payload.get("director_id") == "director-B"
    ]
    assert rejected[-1].payload["reason"] == "no_authoritative_eligibility_record"
    assert rejected[-1].correlation_id == f"director-failover:{submission.mission_id}:epoch-2"
