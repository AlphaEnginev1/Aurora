from pathlib import Path
from threading import Event

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy


class FakePlanner:
    def __init__(self):
        self.calls = []

    def plan(self, **kwargs):
        self.calls.append(kwargs)
        return DirectorPlan(
            objective=kwargs["objective"],
            tasks=(
                DelegatedTask("collect", "researcher", "collect evidence"),
                DelegatedTask(
                    "review",
                    "reviewer",
                    "review evidence",
                    depends_on=("collect",),
                ),
            ),
            model="fake/model",
        )


class FakeOrchestration:
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return object()

    def run_capability_task(self, **kwargs):
        raise AssertionError("capability execution is not expected")


def make_runtime(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    planner = FakePlanner()
    engine = TaskEngine(FakeOrchestration(), tmp_path / "workspace")
    director = DurableDirectorService(
        planner=planner,
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    runtime = DirectorRuntime(
        director=director,
        store=store,
        event_bus=bus,
        worker_policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=2,
            idle_sleep_seconds=0,
        ),
        worker_id="director-a",
        event_consumer_id="director-events",
    )
    return runtime, store, planner, engine


def test_full_director_event_bus_worker_cycle(tmp_path: Path):
    runtime, store, planner, engine = make_runtime(tmp_path)

    submission = runtime.submit_mission(objective="research Aurora")
    result = runtime.run_until_terminal(submission)

    assert planner.calls[0]["objective"] == "research Aurora"
    assert result.status is TaskStatus.SUCCEEDED
    assert [item.status for item in result.tasks] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.SUCCEEDED,
    ]

    event_types = [event.event_type for event in store.events(submission.mission_id)]
    assert event_types[:2] == ["enqueued", "enqueued"]
    assert "mission_submitted" in event_types
    assert "mission_started" in event_types
    assert event_types.count("worker.task_succeeded") == 2
    assert "mission_succeeded" in event_types
    assert [call["task_id"] for call in engine.orchestration.calls] == ["collect", "review"]


def test_runtime_restarts_and_recovers_running_mission(tmp_path: Path):
    runtime, store, planner, engine = make_runtime(tmp_path)
    submission = runtime.submit_mission(objective="recover me")

    claimed = store.claim_next(
        submission.mission_id,
        "crashed-worker",
        lease_seconds=1,
    )
    assert claimed is not None
    assert store.mission_status(submission.mission_id) == "running"

    # A new runtime instance sees the same durable mission and the worker
    # requeues the expired lease before continuing the dependency graph.
    import time
    time.sleep(1.1)

    runtime2, _, _, _ = make_runtime(tmp_path)
    result = runtime2.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    events = store.events(submission.mission_id)
    assert any(event.event_type == "requeued" for event in events)


def test_runtime_stop_event_allows_clean_24x7_shutdown(tmp_path: Path):
    runtime, _, _, _ = make_runtime(tmp_path)
    stop = Event()
    stop.set()
    runtime.run_forever(stop)
