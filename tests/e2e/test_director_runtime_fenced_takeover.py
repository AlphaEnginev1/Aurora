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


class _Clock:
    def __init__(self, value: str):
        self.value = value

    def __call__(self):
        return self.value


class _HandoffOrchestration:
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs["task_id"])
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="handoff-runtime-test",
                provider="handoff-runtime-test",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}-{len(self.calls)}",
                execution_id=f"execution-{kwargs['task_id']}-{len(self.calls)}",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("runtime ownership proof does not use capabilities")


class _PlannerA:
    def __init__(self):
        self.plan_calls = 0

    def plan(self, **kwargs):
        self.plan_calls += 1
        return DirectorPlan(
            objective=kwargs["objective"],
            tasks=(
                DelegatedTask("step_a", "agent", "step A"),
                DelegatedTask("step_b", "agent", "step B", depends_on=("step_a",)),
                DelegatedTask("step_c", "agent", "step C", depends_on=("step_b",)),
            ),
            model="director-A",
        )

    def review(self, **kwargs):
        raise AssertionError("Director A should not review during takeover test")


class _PlannerB:
    def __init__(self):
        self.plan_calls = 0
        self.review_calls = 0

    def plan(self, **kwargs):
        self.plan_calls += 1
        raise AssertionError("Director B must not re-plan the persisted task graph")

    def review(self, **kwargs):
        self.review_calls += 1
        return DirectorReview(
            decision="complete",
            rationale="Director B verified the durable mission state.",
            tasks=(),
            model="director-B",
        )


def test_runtime_excludes_competing_director_then_takes_over_with_fencing(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    ownership = DirectorOwnershipStore(store.path)
    orchestration = _HandoffOrchestration()

    # Both Director services share the same task engine/orchestration boundary
    # but have distinct Director identities and runtime event consumers.
    engine_a = TaskEngine(orchestration, tmp_path / "workspace-a")
    engine_b = TaskEngine(orchestration, tmp_path / "workspace-b")

    clock = _Clock("2026-01-01T00:00:00+00:00")

    planner_a = _PlannerA()
    runtime_a = DirectorRuntime(
        director=DurableDirectorService(
            planner=planner_a,
            task_engine=engine_a,
            store=store,
            event_bus=bus,
        ),
        store=store,
        event_bus=bus,
        director_ownership=ownership,
        director_lease_seconds=30,
        ownership_now_fn=clock,
        worker_policy=WorkerPolicy(lease_seconds=30, max_attempts=1, idle_sleep_seconds=0),
        worker_id="director-A",
        event_consumer_id="director-A-events",
        worker_concurrency=1,
        max_review_rounds=1,
    )

    planner_b = _PlannerB()
    runtime_b = DirectorRuntime(
        director=DurableDirectorService(
            planner=planner_b,
            task_engine=engine_b,
            store=store,
            event_bus=bus,
        ),
        store=store,
        event_bus=bus,
        director_ownership=ownership,
        director_lease_seconds=30,
        ownership_now_fn=clock,
        worker_policy=WorkerPolicy(lease_seconds=30, max_attempts=1, idle_sleep_seconds=0),
        worker_id="director-B",
        event_consumer_id="director-B-events",
        worker_concurrency=1,
        max_review_rounds=1,
    )

    submission = runtime_a.submit_mission(
        objective="Continue one mission across Director replacement without duplicate work."
    )

    first = runtime_a.run_once()
    assert first.did_work
    assert orchestration.calls == ["step_a"]
    assert planner_a.plan_calls == 1

    # A's lease is still valid: B must not execute or mutate the mission.
    clock.value = "2026-01-01T00:00:10+00:00"
    blocked_cycle = runtime_b.run_once()
    assert not blocked_cycle.worker_cycles
    assert orchestration.calls == ["step_a"]

    # A is now unavailable. B takes over with a higher fencing epoch.
    clock.value = "2026-01-01T00:00:31+00:00"
    takeover_cycle = runtime_b.run_once()
    assert takeover_cycle.did_work
    assert orchestration.calls == ["step_a", "step_b"]
    assert planner_b.plan_calls == 0

    # A is stale and must not be allowed to terminalize the live mission.
    runtime_a._publish_terminal(
        submission.mission_id,
        status="failed",
        reason="stale Director attempt",
    )

    events = store.events(submission.mission_id)
    assert any(event.event_type == "director_ownership_takeover" for event in events)
    assert any(event.event_type == "director_ownership_rejected" for event in events)
    assert any(event.event_type == "director_ownership_fence_rejected" for event in events)
    assert not any(
        event.event_type == "mission_failed"
        and event.payload.get("reason") == "stale Director attempt"
        for event in events
    )

    # B continues the same persisted mission and completes the remaining work.
    result = runtime_b.run_until_terminal(submission, max_cycles=20)

    assert result.status is TaskStatus.SUCCEEDED
    assert orchestration.calls == ["step_a", "step_b", "step_c"]
    assert planner_b.plan_calls == 0
    assert planner_b.review_calls == 1

    tasks = {item.task.task_id: item for item in store.list_tasks(submission.mission_id)}
    assert all(tasks[key].status == "succeeded" for key in ("step_a", "step_b", "step_c"))
    assert all(tasks[key].attempts == 1 for key in ("step_a", "step_b", "step_c"))

    terminal_events = [
        event for event in store.events(submission.mission_id)
        if event.event_type.startswith("mission_")
    ]
    assert [event.event_type for event in terminal_events] == ["mission_succeeded"]

    current = ownership.get(submission.mission_id)
    assert current is not None
    assert current.owner_id == "director-B"
    assert current.fencing_epoch == 2
