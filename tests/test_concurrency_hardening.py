import pytest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import TaskWorker, WorkerPolicy


class _FakeTaskEngine:
    def execute_one(self, task, *, execution_scope=None):
        return type(
            "Result",
            (),
            {
                "task": task,
                "status": TaskStatus.SUCCEEDED,
                "model_result": None,
                "capability_result": None,
                "error": None,
            },
        )()


def test_concurrent_workers_claim_each_task_once(tmp_path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    tasks = tuple(
        DelegatedTask(
            task_id=f"task-{index}",
            role="research",
            objective=f"work {index}",
        )
        for index in range(8)
    )
    store.enqueue("mission-1", tasks)

    def run(worker_number):
        worker = TaskWorker(
            store=store,
            task_engine=_FakeTaskEngine(),
            mission_id="mission-1",
            worker_id=f"worker-{worker_number}",
            policy=WorkerPolicy(lease_seconds=30, idle_sleep_seconds=0),
        )
        return worker.run_once()

    with ThreadPoolExecutor(max_workers=8) as pool:
        cycles = tuple(pool.map(run, range(8)))

    completed = [cycle.task_id for cycle in cycles if cycle.task_id is not None]
    assert len(completed) == 8
    assert len(set(completed)) == 8
    assert {store.get("mission-1", task_id).status for task_id in completed} == {"succeeded"}


class _CapabilityRecorder:
    def __init__(self):
        self.workspaces = []

    def run_capability_task(self, *, task_id, role, request, workspace, args):
        self.workspaces.append(Path(workspace))
        # Match the brokered CapabilityExecutionResult contract: no Workbench
        # status field, but an explicit zero exit code and timeout signal.
        return type(
            "CapabilityExecution",
            (),
            {
                "returncode": 0,
                "stdout": "capability completed",
                "stderr": "",
                "timed_out": False,
            },
        )()


def test_task_engine_assigns_distinct_attempt_workspaces(tmp_path):
    recorder = _CapabilityRecorder()
    engine = TaskEngine(recorder, tmp_path)

    tasks = [
        DelegatedTask("task-a", "engineer", "build A", capability="tool-a"),
        DelegatedTask("task-b", "engineer", "build B", capability="tool-b"),
    ]
    results = [
        engine.execute_one(task, execution_scope=f"mission-1:{task.task_id}:attempt:1")
        for task in tasks
    ]

    assert all(result.status is TaskStatus.SUCCEEDED for result in results)
    assert len(recorder.workspaces) == 2
    assert recorder.workspaces[0] != recorder.workspaces[1]
    assert all(path.parent.name == "tasks" for path in recorder.workspaces)
    assert all(path.is_dir() for path in recorder.workspaces)


def test_stale_claim_cannot_complete_after_lease_recovery(tmp_path):
    from aurora.orchestration.task_store import TaskStoreError

    store = DurableTaskStore(tmp_path / "tasks.db")
    task = DelegatedTask("fenced", "engineer", "fenced work")
    store.enqueue("mission-fence", [task])

    first = store.claim_next("mission-fence", "worker-a", lease_seconds=1)
    assert first is not None
    assert first.claim_token

    store.recover_expired(
        "mission-fence",
        now="9999-01-01T00:00:00+00:00",
    )
    second = store.claim_next("mission-fence", "worker-b", lease_seconds=30)
    assert second is not None
    assert second.claim_token != first.claim_token

    with pytest.raises(TaskStoreError):
        store.complete(
            "mission-fence",
            "fenced",
            status="succeeded",
            worker_id="worker-a",
            claim_token=first.claim_token,
            result={"owner": "stale"},
        )

    store.complete(
        "mission-fence",
        "fenced",
        status="succeeded",
        worker_id="worker-b",
        claim_token=second.claim_token,
        result={"owner": "current"},
    )
    assert store.get("mission-fence", "fenced").result == {"owner": "current"}
