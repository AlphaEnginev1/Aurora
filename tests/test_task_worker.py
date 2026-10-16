from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from aurora.inference.models import InferenceResponse
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import (
    DelegatedTask,
    DelegatedTaskResult,
    ResearchTaskResult,
    TaskEngine,
    TaskStatus,
    research_learning_is_authorized,
)
from aurora.orchestration.task_store import DurableTaskStore, TaskStoreError
from aurora.orchestration.task_worker import TaskWorker, WorkerPolicy
from aurora.research.models import EvidenceState
from aurora.skills.learning import SkillLearningStore


class FakeOrchestration:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("transient failure")
        return TaskResult(
            task_id=kwargs["task_id"],
            role=kwargs["role"],
            response=InferenceResponse(
                model="fake/model",
                provider="fake",
                content=f"completed {kwargs['task_id']}",
                request_id=f"request-{kwargs['task_id']}",
                execution_id=f"execution-{kwargs['task_id']}",
                requested_model="fake/model",
                selected_model="fake/model",
                reported_model="fake/model",
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("capability path is not expected in this test")


def make_worker(tmp_path: Path, *, failures: int = 0, max_attempts: int = 3):
    orchestration = FakeOrchestration(failures=failures)
    engine = TaskEngine(orchestration, tmp_path / "workspace")
    store = DurableTaskStore(tmp_path / "tasks.db")
    worker = TaskWorker(
        store=store,
        task_engine=engine,
        mission_id="mission-1",
        worker_id="worker-a",
        policy=WorkerPolicy(
            lease_seconds=30,
            max_attempts=max_attempts,
            idle_sleep_seconds=0,
        ),
    )
    return store, worker, orchestration


def test_worker_executes_dependency_chain_durably(tmp_path: Path):
    store, worker, orchestration = make_worker(tmp_path)
    store.enqueue(
        "mission-1",
        (
            DelegatedTask("a", "researcher", "collect"),
            DelegatedTask("b", "reviewer", "review", depends_on=("a",)),
        ),
    )

    cycles = worker.run_until_idle(max_cycles=4)

    assert [cycle.action for cycle in cycles] == ["succeeded", "succeeded", "idle"]
    assert [item.status for item in store.list_tasks("mission-1")] == [
        "succeeded",
        "succeeded",
    ]
    assert [call["task_id"] for call in orchestration.calls] == ["a", "b"]


def test_worker_retries_transient_failure_without_losing_task(tmp_path: Path):
    store, worker, orchestration = make_worker(
        tmp_path,
        failures=1,
        max_attempts=2,
    )
    store.enqueue(
        "mission-1",
        [DelegatedTask("a", "agent", "work")],
    )

    first = worker.run_once()
    second = worker.run_once()

    assert first.action == "retried"
    assert second.action == "succeeded"
    task = store.get("mission-1", "a")
    assert task is not None
    assert task.status == "succeeded"
    assert task.attempts == 2
    assert len(orchestration.calls) == 2
    assert [event.event_type for event in store.events("mission-1")] == [
        "enqueued",
        "claimed",
        "retry_scheduled",
        "claimed",
        "succeeded",
    ]


def test_worker_fails_after_max_attempts_and_blocks_dependents(tmp_path: Path):
    store, worker, _ = make_worker(tmp_path, failures=5, max_attempts=1)
    store.enqueue(
        "mission-1",
        (
            DelegatedTask("a", "agent", "fail"),
            DelegatedTask("b", "agent", "dependent", depends_on=("a",)),
        ),
    )

    cycle = worker.run_once()

    assert cycle.action == "failed"
    assert cycle.blocked_tasks == 1
    assert store.get("mission-1", "a").status == "failed"
    assert store.get("mission-1", "b").status == "blocked"


def test_expired_worker_lease_is_requeued_after_restart(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-1", [DelegatedTask("a", "agent", "work")])

    claimed = store.claim_next("mission-1", "worker-crashed", lease_seconds=30)
    assert claimed is not None
    assert claimed.claimed_by == "worker-crashed"

    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    assert store.recover_expired("mission-1", now=future.isoformat()) == 1

    recovered = store.claim_next("mission-1", "worker-restarted", lease_seconds=30)
    assert recovered is not None
    assert recovered.task.task_id == "a"
    assert recovered.attempts == 2
    assert recovered.claimed_by == "worker-restarted"

    events = store.events("mission-1")
    assert [event.event_type for event in events] == [
        "enqueued",
        "claimed",
        "requeued",
        "claimed",
    ]


def test_lease_renewal_requires_owner(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    store.enqueue("mission-1", [DelegatedTask("a", "agent", "work")])
    claimed = store.claim_next("mission-1", "worker-a", lease_seconds=30)
    assert claimed is not None

    try:
        store.renew_lease("mission-1", "a", "worker-b", lease_seconds=30)
    except TaskStoreError as exc:
        assert "does not own" in str(exc)
    else:
        raise AssertionError("wrong worker unexpectedly renewed the lease")

    renewed = store.renew_lease("mission-1", "a", "worker-a", lease_seconds=30)
    assert renewed > claimed.lease_until


def test_run_until_idle_rejects_nonconvergent_worker(tmp_path: Path):
    store, worker, _ = make_worker(tmp_path, failures=0)
    store.enqueue("mission-1", [DelegatedTask("a", "agent", "work")])

    # One cycle is not enough: the worker must execute then observe idle.
    try:
        worker.run_until_idle(max_cycles=1)
    except Exception as exc:
        assert "max_cycles" in str(exc)
    else:
        raise AssertionError("worker unexpectedly reported convergence")

def test_worker_persists_routing_evidence_for_director_review(tmp_path: Path):
    response = InferenceResponse(
        model="model-a",
        provider="nvidia",
        content="completed",
        selected_model="model-a",
        routing_score=0.83,
        routing_factors={"learned_score": 0.9, "quality": 0.8},
        routing_candidates=(("model-a", 0.83), ("model-b", 0.71)),
    )
    result = DelegatedTaskResult(
        task=DelegatedTask("a", "agent", "work"),
        status=TaskStatus.SUCCEEDED,
        model_result=TaskResult(task_id="a", role="agent", response=response),
    )

    payload = TaskWorker._result_payload(result)

    assert payload["routing"] == {
        "selected_model": "model-a",
        "score": 0.83,
        "factors": {"learned_score": 0.9, "quality": 0.8},
        "candidates": [
            {"model": "model-a", "score": 0.83},
            {"model": "model-b", "score": 0.71},
        ],
    }



def test_task_engine_rejects_capability_nonzero_exit_and_timeout(tmp_path: Path):
    engine = TaskEngine(FakeOrchestration(), tmp_path / "workspace")
    task = DelegatedTask(
        "capability-execution",
        "agent",
        "run the registered capability",
        capability="registered:capability",
    )
    executions = (
        (
            SimpleNamespace(
                returncode=17,
                stdout="partial output retained",
                stderr="command failed",
                timed_out=False,
            ),
            "returncode",
        ),
        (
            SimpleNamespace(
                returncode=0,
                stdout="partial output retained",
                stderr="",
                timed_out=True,
            ),
            "timed out",
        ),
        (
            SimpleNamespace(
                status="succeeded",
                returncode=8,
                stdout="partial output retained",
                stderr="late process failure",
                timed_out=False,
            ),
            "returncode",
        ),
    )

    for execution, expected_reason in executions:
        engine._execute_task = lambda _task, **_kwargs: SimpleNamespace(execution=execution)
        result = engine.execute_one(task)
        assert result.status is TaskStatus.FAILED
        assert result.capability_result.execution is execution
        assert expected_reason in (result.error or "")


def test_worker_preserves_capability_failure_receipt_at_attempt_limit(tmp_path: Path):
    store, worker, _ = make_worker(tmp_path, max_attempts=1)
    task = DelegatedTask(
        "capability-failed",
        "agent",
        "run a capability and preserve its receipt",
        capability="registered:capability",
    )
    store.enqueue("mission-1", [task])
    execution = SimpleNamespace(
        returncode=17,
        stdout="partial stdout that explains the failure",
        stderr="controlled failure detail",
        timed_out=True,
    )
    worker.task_engine.execute_one = lambda _task, **_kwargs: DelegatedTaskResult(
        task=task,
        status=TaskStatus.FAILED,
        capability_result=SimpleNamespace(execution=execution),
        error="controlled capability failure",
    )

    cycle = worker.run_once()

    assert cycle.action == "failed"
    assert cycle.result_payload["type"] == "capability"
    assert cycle.result_payload["attempt"] == 1
    assert cycle.result_payload["returncode"] == 17
    assert cycle.result_payload["timed_out"] is True
    assert cycle.result_payload["stdout"] == execution.stdout
    assert cycle.result_payload["stderr"] == execution.stderr
    assert cycle.result_payload["error"] == "controlled capability failure"
    stored = store.get("mission-1", task.task_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.result == cycle.result_payload


class _CountingEvaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, **kwargs):
        self.calls += 1
        return ()


class _CountingLearningStore:
    def __init__(self):
        self.records = []

    def record(self, evaluation):
        self.records.append(evaluation)


def _claimed_task_for_learning(tmp_path: Path, task: DelegatedTask):
    store, worker, _ = make_worker(tmp_path)
    store.enqueue("mission-1", [task])
    claimed = store.claim_next("mission-1", "worker-a", lease_seconds=30)
    assert claimed is not None
    return store, worker, claimed


def test_unverified_research_result_cannot_enter_worker_or_engine_learning(tmp_path: Path):
    task = DelegatedTask(
        "research-task",
        "researcher",
        "retrieve candidate research",
        capability="research",
        skills=("research-skill",),
    )
    _, worker, claimed = _claimed_task_for_learning(tmp_path, task)
    evaluator = _CountingEvaluator()
    learning = SkillLearningStore(tmp_path / "skill-learning.db")
    organization = SimpleNamespace(
        resolve_agent=lambda role: SimpleNamespace(
            role=role,
            skill_ids=("research-skill",),
            capabilities=frozenset(),
        )
    )
    worker.skill_evaluator = evaluator
    worker.skill_learning = learning
    worker.task_engine.skill_learning = learning
    worker.task_engine.skill_registry = object()
    worker.task_engine.organization = organization

    result = DelegatedTaskResult(
        task=task,
        status=TaskStatus.SUCCEEDED,
        capability_result=ResearchTaskResult(
            request_id="research-untrusted",
            source_ids=("source-untrusted",),
            claim_ids=("claim-unverified",),
            trust_state=EvidenceState.INSUFFICIENT,
            knowledge_artifact_ids=(),
        ),
    )

    assert not research_learning_is_authorized(result)
    assert worker.task_engine._learn_from_result(result) is result
    worker._learn_from_result(claimed, result)
    # If the worker skipped its trust gate it would invoke this evaluator using
    # the agent's default skill, despite no trusted knowledge artifact.
    assert evaluator.calls == 0
    assert learning.performance("research-skill").evaluations == 0


def test_raw_workbench_research_retrieval_cannot_enter_skill_learning(
    tmp_path: Path, monkeypatch
):
    task = DelegatedTask(
        "workbench-research",
        "researcher",
        "retrieve and extract candidate claims",
        capability="workbench:research:retrieve_extract",
        skills=("research-skill",),
    )
    _, worker, claimed = _claimed_task_for_learning(tmp_path, task)
    evaluator = _CountingEvaluator()
    learning = SkillLearningStore(tmp_path / "skill-learning.db")
    organization = SimpleNamespace(
        resolve_agent=lambda role: SimpleNamespace(
            role=role,
            skill_ids=("research-skill",),
            capabilities=frozenset(),
        )
    )
    worker.skill_evaluator = evaluator
    worker.skill_learning = learning
    worker.task_engine.skill_learning = learning
    worker.task_engine.skill_registry = object()
    worker.task_engine.organization = organization
    monkeypatch.setattr(
        "aurora.skills.evaluation.SkillEvaluator",
        lambda registry: evaluator,
    )
    result = DelegatedTaskResult(
        task=task,
        status=TaskStatus.SUCCEEDED,
        capability_result=SimpleNamespace(
            execution=SimpleNamespace(
                status="succeeded",
                operation="research_retrieve_extract",
                output={"claims": [{"claim": "unverified candidate"}]},
            )
        ),
    )

    assert not research_learning_is_authorized(result)
    assert worker.task_engine._learn_from_result(result) is result
    worker._learn_from_result(claimed, result)
    assert evaluator.calls == 0
    assert learning.performance("research-skill").evaluations == 0


def test_verified_research_artifact_may_cross_learning_gate():
    result = DelegatedTaskResult(
        task=DelegatedTask(
            "verified-research",
            "researcher",
            "summarize verified knowledge",
            capability="research",
        ),
        status=TaskStatus.SUCCEEDED,
        capability_result=ResearchTaskResult(
            request_id="research-verified",
            source_ids=("source-a", "source-b"),
            claim_ids=("claim-verified",),
            trust_state=EvidenceState.VERIFIED,
            knowledge_artifact_ids=("kart-verified",),
        ),
    )

    assert research_learning_is_authorized(result)


def test_worker_persists_structured_workbench_output_and_receipt_metadata():
    task = DelegatedTask(
        "research-output",
        "researcher",
        "retrieve source metadata",
        capability="workbench:research:retrieve",
    )
    execution = SimpleNamespace(
        status="succeeded",
        operation="research_retrieve",
        output={"sources": [{"source_id": "src-1", "url": "https://example.test"}]},
        artifact_id="artifact-1",
        provenance="aurora-research:retrieval",
        receipt_id="receipt-1",
        correlation_id="corr-1",
    )
    result = DelegatedTaskResult(
        task=task,
        status=TaskStatus.SUCCEEDED,
        capability_result=SimpleNamespace(execution=execution),
    )

    payload = TaskWorker._result_payload(result)

    assert payload["type"] == "workbench"
    assert payload["output"] == {
        "sources": [{"source_id": "src-1", "url": "https://example.test"}]
    }
    assert payload["artifact_id"] == "artifact-1"
    assert payload["provenance"] == "aurora-research:retrieval"
    assert payload["receipt_id"] == "receipt-1"
    assert payload["correlation_id"] == "corr-1"
