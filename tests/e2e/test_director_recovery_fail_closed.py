from pathlib import Path
import sqlite3

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan
from aurora.orchestration.director_handoff_store import DirectorHandoffStore
from aurora.orchestration.director_ownership import DirectorOwnershipStore
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine
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
                model="gate-7-test-model",
                provider="gate-7-test",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("Gate 7 does not use capability execution")


class _Planner:
    def __init__(self, *, allow_review=False):
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
            model="gate-7-director",
        )

    def review(self, **kwargs):
        self.review_calls += 1
        if not self.allow_review:
            raise AssertionError("contradiction recovery must stop before Director review")
        return type("Review", (), {
            "decision": "complete",
            "rationale": "existing durable state is terminal",
            "tasks": (),
            "model": "gate-7-director",
        })()


class _Clock:
    def __init__(self, value="2026-01-01T00:00:00+00:00"):
        self.value = value

    def __call__(self):
        return self.value


def _runtime(*, store, bus, ownership, handoff, worker_id, clock, planner):
    engine = TaskEngine(_Orchestration(), Path(store.path).parent / "workspace")
    return DirectorRuntime(
        director=DurableDirectorService(
            planner=planner,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        store=store,
        director_ownership=ownership,
        director_handoff_store=handoff,
        director_lease_seconds=30,
        ownership_now_fn=clock,
        event_bus=bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=1,
            idle_sleep_seconds=0,
        ),
        worker_id=worker_id,
        event_consumer_id=f"{worker_id}-events",
        worker_concurrency=1,
        max_review_rounds=1,
    )


def _prepare(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "state.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    handoff = DirectorHandoffStore(store.path)
    clock = _Clock()
    planner = _Planner()

    runtime = _runtime(
        store=store,
        bus=bus,
        ownership=ownership,
        handoff=handoff,
        worker_id="director-A",
        clock=clock,
        planner=planner,
    )
    submission = runtime.submit_mission(
        objective="Prove Gate 7 fail-closed recovery safety."
    )
    runtime.run_once()

    assert [e for e in store.events(submission.mission_id) if e.event_type == "task_succeeded"]
    assert store.get(submission.mission_id, "step_a").status == "succeeded"
    assert store.get(submission.mission_id, "step_b").status == "pending"

    owner = ownership.get(submission.mission_id)
    assert owner is not None and owner.owner_id == "director-A"
    return store, bus, ownership, handoff, clock, submission, owner, planner


def _set_ownership_contradiction(path, mission_id, owner_id, epoch, lease_until):
    with sqlite3.connect(path) as db:
        db.execute(
            """
            UPDATE director_ownership
            SET owner_id = ?, fencing_epoch = ?, lease_until = ?, updated_at = ?
            WHERE mission_id = ?
            """,
            (owner_id, epoch, lease_until, lease_until, mission_id),
        )
        db.commit()


def _set_handoff_acquired_contradiction(path, handoff_id, successor_epoch):
    with sqlite3.connect(path) as db:
        db.execute(
            """
            UPDATE director_handoffs
            SET state = 'ACQUIRED',
                successor_epoch = ?,
                version = version + 1
            WHERE handoff_id = ?
            """,
            (successor_epoch, handoff_id),
        )
        db.commit()


def _append_terminal_event(path, mission_id):
    with sqlite3.connect(path) as db:
        db.execute(
            """
            INSERT INTO task_events (
                mission_id, task_id, event_type, payload_json, created_at
            ) VALUES (?, NULL, 'mission_succeeded', ?, ?)
            """,
            (
                mission_id,
                '{"status":"succeeded","reason":"contradictory fixture"}',
                "2026-01-01T00:00:03+00:00",
            ),
        )
        db.commit()


def _diagnostics(store, mission_id):
    return [
        event for event in store.events(mission_id)
        if event.event_type == "director_recovery_fail_closed"
    ]


def test_gate_7_handoff_ownership_mismatch_stops_mutation(tmp_path: Path):
    store, bus, ownership, handoff, clock, submission, owner, planner = _prepare(tmp_path)
    runtime_a = _runtime(
        store=store, bus=bus, ownership=ownership, handoff=handoff,
        worker_id="director-A", clock=clock, planner=planner,
    )
    released = runtime_a.request_director_handoff(
        submission.mission_id,
        "director-B",
        handoff_id="gate-7-mismatch-001",
        now="2026-01-01T00:00:01+00:00",
    )
    assert released.state == "RELEASED"

    _set_ownership_contradiction(
        store.path,
        submission.mission_id,
        "director-C",
        7,
        "2026-01-01T00:01:00+00:00",
    )
    before_handoff = handoff.get(released.handoff_id)
    before_owner = ownership.get(submission.mission_id)

    runtime_b = _runtime(
        store=store, bus=bus, ownership=ownership,
        handoff=DirectorHandoffStore(store.path),
        worker_id="director-B", clock=clock, planner=_Planner(),
    )
    runtime_b._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="must not mutate contradictory recovery state",
    )

    assert handoff.get(released.handoff_id) == before_handoff
    assert ownership.get(submission.mission_id) == before_owner
    assert not any(
        event.event_type == "mission_failed"
        and event.payload.get("reason") == "must not mutate contradictory recovery state"
        for event in store.events(submission.mission_id)
    )
    assert _diagnostics(store, submission.mission_id)[-1].payload["reason_code"] == "HANDOFF_OWNERSHIP_MISMATCH"
    assert planner.plan_calls == 1
    assert planner.review_calls == 0


def test_gate_7_successor_epoch_mismatch_stops_mutation(tmp_path: Path):
    store, bus, ownership, handoff, clock, submission, owner, _ = _prepare(tmp_path)
    runtime_a = _runtime(
        store=store, bus=bus, ownership=ownership, handoff=handoff,
        worker_id="director-A", clock=clock, planner=_Planner(),
    )
    released = runtime_a.request_director_handoff(
        submission.mission_id,
        "director-B",
        handoff_id="gate-7-mismatch-002",
        now="2026-01-01T00:00:01+00:00",
    )
    assert released.state == "RELEASED"

    # Construct an impossible but durable state: handoff says B owns epoch 2,
    # ownership authority says B owns epoch 3.
    _set_handoff_acquired_contradiction(store.path, released.handoff_id, 2)
    _set_ownership_contradiction(
        store.path,
        submission.mission_id,
        "director-B",
        3,
        "2026-01-01T00:01:00+00:00",
    )

    before_handoff = DirectorHandoffStore(store.path).get(released.handoff_id)
    before_owner = ownership.get(submission.mission_id)

    runtime_b = _runtime(
        store=DurableTaskStore(store.path),
        bus=bus,
        ownership=DirectorOwnershipStore(store.path),
        handoff=DirectorHandoffStore(store.path),
        worker_id="director-B",
        clock=clock,
        planner=_Planner(),
    )
    runtime_b._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="must stop on successor epoch contradiction",
    )

    assert DirectorHandoffStore(store.path).get(released.handoff_id) == before_handoff
    assert DirectorOwnershipStore(store.path).get(submission.mission_id) == before_owner
    assert not any(
        event.event_type == "mission_failed"
        and event.payload.get("reason") == "must stop on successor epoch contradiction"
        for event in DurableTaskStore(store.path).events(submission.mission_id)
    )
    assert _diagnostics(DurableTaskStore(store.path), submission.mission_id)[-1].payload["reason_code"] == "OWNERSHIP_SUCCESSOR_EPOCH_MISMATCH"


def test_gate_7_terminal_task_contradiction_stops_recovery(tmp_path: Path):
    store, bus, ownership, handoff, clock, submission, owner, _ = _prepare(tmp_path)
    _append_terminal_event(store.path, submission.mission_id)

    restarted_store = DurableTaskStore(store.path)
    restarted_ownership = DirectorOwnershipStore(store.path)
    restarted_handoff = DirectorHandoffStore(store.path)
    restarted_runtime = _runtime(
        store=restarted_store,
        bus=bus,
        ownership=restarted_ownership,
        handoff=restarted_handoff,
        worker_id="director-A",
        clock=clock,
        planner=_Planner(),
    )

    before_tasks = tuple(restarted_store.list_tasks(submission.mission_id))
    restarted_runtime._review_or_finalize(submission.mission_id)
    after_tasks = tuple(restarted_store.list_tasks(submission.mission_id))

    assert after_tasks == before_tasks
    terminal = [
        event for event in restarted_store.events(submission.mission_id)
        if event.event_type in {"mission_succeeded", "mission_failed", "mission_blocked"}
    ]
    assert [event.event_type for event in terminal] == ["mission_succeeded"]
    assert _diagnostics(restarted_store, submission.mission_id)[-1].payload["reason_code"] == "TERMINAL_MISSION_WITH_RUNNABLE_TASKS"


def test_gate_7_contradiction_survives_full_restart(tmp_path: Path):
    store, bus, ownership, handoff, clock, submission, owner, planner = _prepare(tmp_path)
    runtime_a = _runtime(
        store=store, bus=bus, ownership=ownership, handoff=handoff,
        worker_id="director-A", clock=clock, planner=planner,
    )
    released = runtime_a.request_director_handoff(
        submission.mission_id,
        "director-B",
        handoff_id="gate-7-mismatch-004",
        now="2026-01-01T00:00:01+00:00",
    )
    _set_ownership_contradiction(
        store.path,
        submission.mission_id,
        "director-C",
        9,
        "2026-01-01T00:01:00+00:00",
    )

    # Fresh process-equivalent objects must see the same contradiction.
    restarted_store = DurableTaskStore(store.path)
    restarted_ownership = DirectorOwnershipStore(store.path)
    restarted_handoff = DirectorHandoffStore(store.path)
    before_owner = restarted_ownership.get(submission.mission_id)
    before_handoff = restarted_handoff.get(released.handoff_id)

    restarted_runtime = _runtime(
        store=restarted_store,
        bus=bus,
        ownership=restarted_ownership,
        handoff=restarted_handoff,
        worker_id="director-B",
        clock=clock,
        planner=_Planner(),
    )
    restarted_runtime._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="restart must remain fail-closed",
    )

    assert restarted_ownership.get(submission.mission_id) == before_owner
    assert restarted_handoff.get(released.handoff_id) == before_handoff
    assert not any(
        event.event_type == "mission_failed"
        and event.payload.get("reason") == "restart must remain fail-closed"
        for event in restarted_store.events(submission.mission_id)
    )
    assert _diagnostics(restarted_store, submission.mission_id)[-1].payload["reason_code"] == "HANDOFF_OWNERSHIP_MISMATCH"
