from pathlib import Path
import sqlite3

import pytest

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.director_eligibility import (
    DirectorEligibilityAmbiguous,
    DirectorEligibilityStore,
    DirectorEligibilityUnavailable,
)
from aurora.orchestration.director_handoff_store import DirectorHandoffStore
from aurora.orchestration.director_ownership import DirectorOwnershipStore
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy


class _Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self):
        return self.value


class _Orchestration:
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs["task_id"])
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="eligibility-test-model",
                provider="eligibility-test",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("eligibility acceptance uses deterministic task execution only")


class _Planner:
    def __init__(self, name: str):
        self.name = name
        self.plan_calls = 0
        self.review_calls = 0

    def plan(self, **kwargs):
        self.plan_calls += 1
        return DirectorPlan(
            objective=kwargs["objective"],
            tasks=(DelegatedTask("step_a", "agent", "complete the step"),),
            model=self.name,
        )

    def review(self, **kwargs):
        self.review_calls += 1
        return DirectorReview(
            decision="complete",
            rationale="deterministic eligibility acceptance",
            tasks=(),
            model=self.name,
        )


def _register(
    store: DirectorEligibilityStore,
    director_id: str,
    *,
    state: str = DirectorEligibilityStore.ELIGIBLE,
    ready: bool = True,
    last_seen_at: str = "2026-01-01T00:00:00+00:00",
    freshness_seconds: float = 60,
    generation: int | None = None,
):
    return store.register(
        director_id,
        state=state,
        ready=ready,
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
    eligibility: DirectorEligibilityStore | None = None,
    director_id: str | None = None,
    event_consumer_id: str,
    clock: _Clock,
    workspace: Path,
):
    engine = TaskEngine(_Orchestration(), workspace)
    director = DurableDirectorService(
        planner=_Planner(director_id or "resolved-director"),
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    return DirectorRuntime(
        director=director,
        store=store,
        director_ownership=ownership,
        director_handoff_store=DirectorHandoffStore(store.path),
        director_eligibility=eligibility,
        director_lease_seconds=30,
        ownership_now_fn=clock,
        event_bus=bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=1,
            idle_sleep_seconds=0,
        ),
        worker_id=director_id,
        event_consumer_id=event_consumer_id,
        worker_concurrency=1,
        max_review_rounds=1,
    )


def _mission_with_expired_owner(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "state.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    eligibility = DirectorEligibilityStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    _register(eligibility, "director-A")

    runtime_a = _runtime(
        store=store,
        bus=bus,
        ownership=ownership,
        eligibility=eligibility,
        director_id="director-A",
        event_consumer_id="director-A-events",
        clock=clock,
        workspace=tmp_path / "workspace-a",
    )
    submission = runtime_a.submit_mission(objective="Gate 8 eligibility acceptance mission")
    runtime_a.run_once()
    clock.value = "2026-01-01T00:00:31+00:00"
    return submission, store, bus, ownership, eligibility, clock


def test_g8_ea1_only_eligible_director_can_contend(tmp_path: Path):
    submission, store, bus, ownership, eligibility, clock = _mission_with_expired_owner(tmp_path)
    _register(eligibility, "director-B")
    _register(eligibility, "director-C", state=DirectorEligibilityStore.INELIGIBLE)

    runtime_b = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-B", event_consumer_id="director-B-events",
        clock=clock, workspace=tmp_path / "workspace-b",
    )
    runtime_c = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-C", event_consumer_id="director-C-events",
        clock=clock, workspace=tmp_path / "workspace-c",
    )

    assert not runtime_c._ensure_mission_ownership(submission.mission_id)
    assert runtime_b._ensure_mission_ownership(submission.mission_id)

    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-B"
    assert current.fencing_epoch == 2
    rejected = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_eligibility_rejected"
    ]
    assert rejected
    assert rejected[-1].payload["director_id"] == "director-C"


def test_g8_ea2_revocation_before_acquisition_is_rejected(tmp_path: Path):
    submission, store, bus, ownership, eligibility, clock = _mission_with_expired_owner(tmp_path)
    _register(eligibility, "director-B")
    _register(eligibility, "director-B", state=DirectorEligibilityStore.INELIGIBLE)

    runtime_b = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-B", event_consumer_id="director-B-events",
        clock=clock, workspace=tmp_path / "workspace-b",
    )

    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    historical = ownership.get(submission.mission_id)
    assert historical is not None
    assert historical.owner_id == "director-A"
    assert historical.fencing_epoch == 1


def test_g8_ea3_stale_record_is_rejected(tmp_path: Path):
    submission, store, bus, ownership, eligibility, clock = _mission_with_expired_owner(tmp_path)
    _register(
        eligibility,
        "director-B",
        last_seen_at="2025-12-31T23:59:00+00:00",
        freshness_seconds=30,
    )
    runtime_b = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-B", event_consumer_id="director-B-events",
        clock=clock, workspace=tmp_path / "workspace-b",
    )

    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    assert ownership.get(submission.mission_id).fencing_epoch == 1
    assert any(
        event.payload["reason"] == "eligibility_record_stale"
        for event in store.events(submission.mission_id)
        if event.event_type == "director_eligibility_rejected"
    )


class _UnavailableEligibility(DirectorEligibilityStore):
    def evaluate(self, *args, **kwargs):
        raise DirectorEligibilityUnavailable("authoritative eligibility source unavailable")


def test_g8_ea4_unavailable_authority_fails_closed(tmp_path: Path):
    submission, store, bus, ownership, _, clock = _mission_with_expired_owner(tmp_path)
    unavailable = _UnavailableEligibility(store.path)
    runtime_b = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=unavailable,
        director_id="director-B", event_consumer_id="director-B-events",
        clock=clock, workspace=tmp_path / "workspace-b",
    )

    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    assert ownership.get(submission.mission_id).fencing_epoch == 1
    events = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_eligibility_rejected"
    ]
    assert events
    assert events[-1].payload["source"] == "authoritative_durable_eligibility"


def test_g8_ea5_contradictory_current_records_fail_closed(tmp_path: Path):
    submission, store, bus, ownership, eligibility, clock = _mission_with_expired_owner(tmp_path)
    _register(eligibility, "director-B", generation=2)
    with sqlite3.connect(eligibility.path) as db:
        db.execute(
            """
            INSERT INTO director_eligibility (
                record_id, director_id, generation, state, ready,
                required_capabilities, registration_at, updated_at,
                last_seen_at, freshness_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "eligibility-contradictory",
                "director-B",
                2,
                DirectorEligibilityStore.INELIGIBLE,
                1,
                '["durable_mission_access","durable_ownership_access"]',
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:01+00:00",
                "2026-01-01T00:00:01+00:00",
                60,
            ),
        )

    with pytest.raises(DirectorEligibilityAmbiguous):
        eligibility.current("director-B")

    runtime_b = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-B", event_consumer_id="director-B-events",
        clock=clock, workspace=tmp_path / "workspace-b",
    )
    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    assert ownership.get(submission.mission_id).fencing_epoch == 1


def test_g8_ea6_restart_reconstructs_same_stable_identity_and_state(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "state.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    eligibility = DirectorEligibilityStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    _register(eligibility, "director-B")

    first = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-B", event_consumer_id="director-B-events-1",
        clock=clock, workspace=tmp_path / "workspace-1",
    )
    second = _runtime(
        store=DurableTaskStore(store.path), bus=bus, ownership=DirectorOwnershipStore(store.path),
        eligibility=DirectorEligibilityStore(store.path), director_id="director-B",
        event_consumer_id="director-B-events-2", clock=clock, workspace=tmp_path / "workspace-2",
    )

    assert first.worker_id == "director-B"
    assert second.worker_id == "director-B"
    assert first.worker_id == second.worker_id
    record = eligibility.current("director-B")
    assert record is not None
    assert record.state == DirectorEligibilityStore.ELIGIBLE
    assert record.generation == 1


def test_g8_ea7_eligibility_does_not_grant_existing_mission_ownership(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "state.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    eligibility = DirectorEligibilityStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    _register(eligibility, "director-A")
    _register(eligibility, "director-B")

    runtime_a = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-A", event_consumer_id="director-A-events",
        clock=clock, workspace=tmp_path / "workspace-a",
    )
    submission = runtime_a.submit_mission(objective="separate eligibility from ownership")
    runtime_a.run_once()

    clock.value = "2026-01-01T00:00:10+00:00"
    runtime_b = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-B", event_consumer_id="director-B-events",
        clock=clock, workspace=tmp_path / "workspace-b",
    )
    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-A"
    assert current.fencing_epoch == 1

    clock.value = "2026-01-01T00:00:31+00:00"
    assert runtime_b._ensure_mission_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-B"
    assert current.fencing_epoch == 2


def test_g8_ea8_eligible_director_cannot_bypass_any_cooperative_handoff_state(tmp_path: Path):
    submission, store, bus, ownership, eligibility, clock = _mission_with_expired_owner(tmp_path)
    _register(eligibility, "director-B")
    handoff = DirectorHandoffStore(store.path)
    handoff.request(
        submission.mission_id, "director-A", "director-C", 1,
        handoff_id="handoff-eligibility-001", now="2026-01-01T00:00:31+00:00",
    )

    runtime_b = _runtime(
        store=store, bus=bus, ownership=ownership, eligibility=eligibility,
        director_id="director-B", event_consumer_id="director-B-events",
        clock=clock, workspace=tmp_path / "workspace-b",
    )

    # REQUESTED: eligibility cannot bypass the requested handoff.
    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    handoff.begin_quiescing(
        "handoff-eligibility-001", "director-A", 1,
        now="2026-01-01T00:00:32+00:00",
    )
    clock.value = "2026-01-01T00:00:32+00:00"

    # QUIESCING: eligibility still cannot take control.
    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    handoff.mark_released(
        "handoff-eligibility-001", "director-A", 1,
        now="2026-01-01T00:00:33+00:00",
    )
    clock.value = "2026-01-01T00:00:33+00:00"

    # RELEASED: only the designated successor may acquire.
    assert not runtime_b._ensure_mission_ownership(submission.mission_id)

    ownership_b = ownership.acquire(
        submission.mission_id, "director-C",
        lease_seconds=30, now="2026-01-01T00:00:34+00:00",
    )
    handoff.mark_acquired(
        "handoff-eligibility-001", "director-C", 1, ownership_b.fencing_epoch,
        now="2026-01-01T00:00:34+00:00",
    )
    clock.value = "2026-01-01T00:00:34+00:00"

    # ACQUIRED: the active handoff remains authoritative; B cannot bypass it.
    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-C"
    assert current.fencing_epoch == 2


def test_g8_eligibility_authority_is_default_and_absence_fails_closed_for_takeover(tmp_path: Path):
    submission, store, bus, ownership, _, clock = _mission_with_expired_owner(tmp_path)

    runtime_b = _runtime(
        store=store,
        bus=bus,
        ownership=ownership,
        eligibility=DirectorEligibilityStore(store.path),
        director_id="director-B",
        event_consumer_id="director-B-events",
        clock=clock,
        workspace=tmp_path / "workspace-b",
    )

    # The production Runtime path creates/uses the durable authority even when
    # the caller does not explicitly inject it. An unregistered candidate cannot
    # self-promote into automatic failover.
    assert isinstance(runtime_b.director_eligibility, DirectorEligibilityStore)

    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-A"
    assert current.fencing_epoch == 1
    assert any(
        event.payload["reason"] == "no_authoritative_eligibility_record"
        for event in store.events(submission.mission_id)
        if event.event_type == "director_eligibility_rejected"
    )


def test_g8_eligibility_allows_first_ownership_but_not_unregistered_takeover(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "state.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    runtime_a = _runtime(
        store=store, bus=bus, ownership=ownership,
        eligibility=DirectorEligibilityStore(store.path),
        director_id="director-A", event_consumer_id="director-A-events",
        clock=clock, workspace=tmp_path / "workspace-a",
    )
    submission = runtime_a.submit_mission(objective="first owner may acquire before failover eligibility")
    assert runtime_a._ensure_mission_ownership(submission.mission_id)

    clock.value = "2026-01-01T00:00:31+00:00"
    runtime_b = _runtime(
        store=DurableTaskStore(store.path),
        bus=bus,
        ownership=DirectorOwnershipStore(store.path),
        eligibility=DirectorEligibilityStore(store.path),
        director_id="director-B",
        event_consumer_id="director-B-events",
        clock=clock,
        workspace=tmp_path / "workspace-b",
    )
    assert not runtime_b._ensure_mission_ownership(submission.mission_id)
    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-A"
    assert current.fencing_epoch == 1
