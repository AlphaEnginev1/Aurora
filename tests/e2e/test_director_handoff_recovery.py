from pathlib import Path

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.director_handoff_store import DirectorHandoffStore
from aurora.orchestration.director_ownership import DirectorOwnershipStore
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy


class _RecoveryOrchestration:
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs["task_id"])
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="handoff-recovery-test-model",
                provider="handoff-recovery-test",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("handoff recovery proof does not use capability execution")


class _PlannerA:
    def __init__(self):
        self.plan_calls = 0
        self.review_calls = 0

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
        self.review_calls += 1
        raise AssertionError("Director A must not review during recovery")


class _PlannerB:
    def __init__(self):
        self.plan_calls = 0
        self.review_calls = 0
        self.review_context = None

    def plan(self, **kwargs):
        self.plan_calls += 1
        raise AssertionError("Director B must not re-plan during recovery")

    def review(self, **kwargs):
        self.review_calls += 1
        self.review_context = kwargs["context"]
        return DirectorReview(
            decision="complete",
            rationale="Recovered durable mission state is sufficient.",
            tasks=(),
            model="director-B",
        )


class _Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self):
        return self.value


def _runtime(*, director, store, bus, ownership, handoff, worker_id, clock):
    return DirectorRuntime(
        director=director,
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


def _prepare_mission(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    handoff = DirectorHandoffStore(store.path)
    clock = _Clock("2026-01-01T00:00:00+00:00")
    orchestration = _RecoveryOrchestration()
    engine = TaskEngine(orchestration, tmp_path / "workspace")

    planner_a = _PlannerA()
    runtime_a = _runtime(
        director=DurableDirectorService(
            planner=planner_a,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        store=store,
        bus=bus,
        ownership=ownership,
        handoff=handoff,
        worker_id="director-A",
        clock=clock,
    )

    submission = runtime_a.submit_mission(
        objective="Prove cooperative Director recovery across process restart."
    )
    runtime_a.run_once()

    assert orchestration.calls == ["step_a"]
    owner = ownership.get(submission.mission_id)
    assert owner is not None and owner.owner_id == "director-A"

    return {
        "store": store,
        "bus": bus,
        "ownership": ownership,
        "handoff": handoff,
        "clock": clock,
        "orchestration": orchestration,
        "engine": engine,
        "planner_a": planner_a,
        "runtime_a": runtime_a,
        "submission": submission,
        "owner_epoch": owner.fencing_epoch,
    }


def test_restart_preserves_requested_and_quiescing_handoff(tmp_path: Path):
    state = _prepare_mission(tmp_path)
    store = state["store"]
    bus = state["bus"]
    ownership = state["ownership"]
    handoff = state["handoff"]
    clock = state["clock"]
    submission = state["submission"]
    orchestration = state["orchestration"]
    engine = state["engine"]
    owner_epoch = state["owner_epoch"]

    requested = handoff.request(
        submission.mission_id,
        "director-A",
        "director-B",
        owner_epoch,
        handoff_id="handoff-recovery-001",
        now="2026-01-01T00:00:01+00:00",
    )
    assert requested.state == "REQUESTED"

    # Simulate A process restart: reconstruct every durable service against the
    # same SQLite state. The requested handoff and successor identity survive.
    restarted_handoff = DirectorHandoffStore(store.path)
    restarted_ownership = DirectorOwnershipStore(store.path)
    restarted_store = DurableTaskStore(store.path)
    assert restarted_handoff.get("handoff-recovery-001").state == "REQUESTED"
    assert restarted_handoff.get_active_for_mission(submission.mission_id).successor_id == "director-B"
    assert restarted_ownership.get(submission.mission_id).fencing_epoch == owner_epoch

    restarted_a = _runtime(
        director=DurableDirectorService(
            planner=_PlannerA(),
            task_engine=engine,
            store=restarted_store,
            event_bus=bus,
        ),
        store=restarted_store,
        bus=bus,
        ownership=restarted_ownership,
        handoff=restarted_handoff,
        worker_id="director-A",
        clock=clock,
    )
    before = len(restarted_store.events(submission.mission_id))
    cycle = restarted_a.run_once()
    after = restarted_store.events(submission.mission_id)
    assert not cycle.worker_cycles
    assert len(after) == before
    assert orchestration.calls == ["step_a"]

    quiescing = restarted_handoff.begin_quiescing(
        "handoff-recovery-001",
        "director-A",
        owner_epoch,
        now="2026-01-01T00:00:02+00:00",
    )
    assert quiescing.state == "QUIESCING"

    # Simulate a second restart while quiescing. A still cannot start new
    # Director-controlled work while the durable handoff is active.
    restarted_again_handoff = DirectorHandoffStore(store.path)
    restarted_again_store = DurableTaskStore(store.path)
    assert restarted_again_handoff.get("handoff-recovery-001").state == "QUIESCING"

    restarted_a_again = _runtime(
        director=DurableDirectorService(
            planner=_PlannerA(),
            task_engine=engine,
            store=restarted_again_store,
            event_bus=bus,
        ),
        store=restarted_again_store,
        bus=bus,
        ownership=DirectorOwnershipStore(store.path),
        handoff=restarted_again_handoff,
        worker_id="director-A",
        clock=clock,
    )
    before = len(restarted_again_store.events(submission.mission_id))
    cycle = restarted_a_again.run_once()
    after = restarted_again_store.events(submission.mission_id)
    assert not cycle.worker_cycles
    assert len(after) == before
    assert orchestration.calls == ["step_a"]


def test_restart_after_release_and_acquisition_preserves_mission(tmp_path: Path):
    state = _prepare_mission(tmp_path)
    store = state["store"]
    bus = state["bus"]
    ownership = state["ownership"]
    handoff = state["handoff"]
    clock = state["clock"]
    submission = state["submission"]
    orchestration = state["orchestration"]
    engine = state["engine"]
    owner_epoch = state["owner_epoch"]
    runtime_a = state["runtime_a"]

    runtime_a.request_director_handoff(
        submission.mission_id,
        "director-B",
        handoff_id="handoff-recovery-002",
        now="2026-01-01T00:00:01+00:00",
    )
    assert handoff.get("handoff-recovery-002").state == "RELEASED"

    # Simulate predecessor restart after release. It cannot recreate
    # authoritative ownership or append a stale terminal mission outcome.
    restarted_a = _runtime(
        director=DurableDirectorService(
            planner=_PlannerA(),
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        store=store,
        bus=bus,
        ownership=DirectorOwnershipStore(store.path),
        handoff=DirectorHandoffStore(store.path),
        worker_id="director-A",
        clock=clock,
    )
    before = len(store.events(submission.mission_id))
    restarted_a._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="stale predecessor after restart",
    )
    after = store.events(submission.mission_id)
    assert len(after) == before + 1
    # The durable RELEASED handoff is still active, so the restarted
    # predecessor is rejected at the handoff boundary before ownership fencing
    # is consulted.
    assert after[-1].event_type == "director_handoff_mutation_rejected"
    assert not any(
        event.event_type == "mission_failed"
        and event.payload.get("reason") == "stale predecessor after restart"
        for event in after
    )
    assert ownership.get(submission.mission_id, now="2026-01-01T00:00:01+00:00") is None

    # B acquires epoch 2, then its process is restarted before executing the
    # unfinished work. A new B instance must reconstruct the durable graph.
    clock.value = "2026-01-01T00:00:02+00:00"
    planner_b = _PlannerB()
    runtime_b = _runtime(
        director=DurableDirectorService(
            planner=planner_b,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        store=store,
        bus=bus,
        ownership=ownership,
        handoff=handoff,
        worker_id="director-B",
        clock=clock,
    )
    assert runtime_b._ensure_mission_ownership(submission.mission_id)
    owner_b = ownership.get(submission.mission_id)
    assert owner_b is not None
    assert owner_b.owner_id == "director-B"
    assert owner_b.fencing_epoch == 2
    assert handoff.get("handoff-recovery-002").state == "COMPLETED"

    # A restart after successor acquisition must now hit the mission-level
    # ownership fence because the cooperative handoff is no longer active.
    restarted_a_after_acquisition = _runtime(
        director=DurableDirectorService(
            planner=_PlannerA(),
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        store=store,
        bus=bus,
        ownership=DirectorOwnershipStore(store.path),
        handoff=DirectorHandoffStore(store.path),
        worker_id="director-A",
        clock=clock,
    )
    before = len(store.events(submission.mission_id))
    restarted_a_after_acquisition._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="stale predecessor after successor acquisition",
    )
    after = store.events(submission.mission_id)
    assert len(after) == before + 1
    assert after[-1].event_type == "director_ownership_rejected"
    assert not any(
        event.event_type == "mission_failed"
        and event.payload.get("reason") == "stale predecessor after successor acquisition"
        for event in after
    )

    restarted_b = _runtime(
        director=DurableDirectorService(
            planner=planner_b,
            task_engine=engine,
            store=store,
            event_bus=bus,
        ),
        store=store,
        bus=bus,
        ownership=ownership,
        handoff=handoff,
        worker_id="director-B",
        clock=clock,
    )
    result = restarted_b.run_until_terminal(submission, max_cycles=20)

    assert result.status is TaskStatus.SUCCEEDED
    assert orchestration.calls == ["step_a", "step_b"]
    assert planner_b.plan_calls == 0
    assert planner_b.review_calls == 1

    tasks = {item.task.task_id: item for item in store.list_tasks(submission.mission_id)}
    assert tasks["step_a"].status == "succeeded"
    assert tasks["step_b"].status == "succeeded"
    assert tasks["step_a"].attempts == 1
    assert tasks["step_b"].attempts == 1

    terminal = [
        event.event_type
        for event in store.events(submission.mission_id)
        if event.event_type in {"mission_succeeded", "mission_failed", "mission_blocked"}
    ]
    assert terminal == ["mission_succeeded"]
