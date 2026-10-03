from pathlib import Path

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
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


def _runtime(*, director, store, bus, worker_id, event_consumer_id, observation_path):
    return DirectorRuntime(
        director=director,
        store=store,
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


def test_director_handoff_reuses_persisted_mission_without_duplicate_execution(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
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
        observation_path=tmp_path / "observability.jsonl",
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
        observation_path=tmp_path / "observability.jsonl",
    )

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
