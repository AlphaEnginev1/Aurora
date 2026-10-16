from pathlib import Path
import pytest
from threading import Event
import hashlib
import json

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.director_decision_comparison import DirectorDecisionComparativeEvidence
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.director_decision_policy_active_store import DirectorDecisionPolicyActiveStore, DirectorDecisionPolicyActive
from aurora.orchestration.director_decision_policy_proposal_store import DirectorDecisionPolicyProposalStore
from aurora.orchestration.director_decision_policy_contract_store import DirectorDecisionPolicyContractStore
from aurora.orchestration.director_decision_policy_evolution import DirectorDecisionPolicyEvolution
from aurora.orchestration.director_decision_knowledge import DirectorDecisionKnowledge
from aurora.orchestration.director_decision_knowledge import DirectorDecisionKnowledge
from aurora.orchestration.director_decision_policy_canary_store import DirectorDecisionPolicyShadowCanaryStore
from aurora.orchestration.director_decision_policy_evolution import DirectorDecisionPolicyEvolution
from aurora.orchestration.director_decision_policy_lifecycle import DirectorDecisionPolicyLifecycle
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy
from aurora.projects.registry import ProjectRegistry, ProjectSpec, ProjectStatus


class FakePlanner:
    def __init__(self, *, max_continue_reviews: int = 1, project_registry=None):
        self.project_registry = project_registry
        self.calls = []
        self.review_calls = 0
        self.review_contexts = []
        self.max_continue_reviews = max_continue_reviews

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

    def review(self, **kwargs):
        self.review_calls += 1
        self.review_contexts.append(kwargs["context"])
        if "adapt" in kwargs["objective"] and self.review_calls <= self.max_continue_reviews:
            return DirectorReview(
                decision="continue",
                rationale="One more validation step is required.",
                tasks=(DelegatedTask("validate", "validator", "validate the result"),),
                model="fake/model",
            )
        return DirectorReview(
            decision="complete",
            rationale="Evidence is sufficient.",
            tasks=(),
            model="fake/model",
        )


class FakeOrchestration:
    def __init__(self):
        self.calls = []

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
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
                provider_model="fake/model",
                reported_model="fake/model",
                routing_score=0.83,
                routing_factors={"learned_score": 0.9, "quality": 0.8},
                routing_candidates=(("fake/model", 0.83), ("fallback/model", 0.71)),
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("capability execution is not expected")


def make_runtime(tmp_path: Path, *, max_continue_reviews: int = 1, project_registry=None):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    planner = FakePlanner(
        max_continue_reviews=max_continue_reviews,
        project_registry=project_registry,
    )
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


def test_policy_shadow_canary_integrates_with_terminal_director_cycle(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    planner = FakePlanner(max_continue_reviews=1)
    engine = TaskEngine(FakeOrchestration(), tmp_path / "workspace")
    director = DurableDirectorService(
        planner=planner,
        task_engine=engine,
        store=store,
        event_bus=bus,
    )
    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-runtime-canary",
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
        observed_direction="better",
        decision_events=2,
        observations=2,
        sources=("independent_execution",),
        claim="Repeated empirical observation.",
        created_at="2026-09-29T19:00:00+00:00",
    )
    proposal = DirectorDecisionPolicyEvolution.propose(
        knowledge,
        proposed_change="Test the complete decision in this recurring comparable context.",
        created_at="2026-09-29T19:01:00+00:00",
    )
    validation = DirectorDecisionPolicyLifecycle.validate(
        proposal,
        holdout_observations=4,
        baseline_consistent=True,
        candidate_consistent=True,
        created_at="2026-09-29T19:02:00+00:00",
    )
    canary = DirectorDecisionPolicyLifecycle.enter_canary(
        proposal,
        validation,
        authorized_by="director-governance",
        created_at="2026-09-29T19:03:00+00:00",
    )
    shadow_store = DirectorDecisionPolicyShadowCanaryStore(tmp_path / "tasks.db")
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
        policy_shadow_activation=canary,
        policy_shadow_proposal=proposal,
        policy_shadow_store=shadow_store,
        policy_shadow_decider=lambda _objective, _context, _proposal: "blocked",
    )

    submission = runtime.submit_mission(objective="research Aurora")
    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    observations = shadow_store.list_for_proposal(proposal.proposal_id)
    assert len(observations) == 1
    observation = observations[0]
    assert observation.baseline_decision == "complete"
    assert observation.candidate_decision == "blocked"
    assert observation.actual_outcome == "mission_succeeded"
    assert observation.decision_agreement is False
    assert observation.baseline_executed is True
    assert observation.candidate_executed is False

    shadow_events = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_decision_policy_shadow_observed"
    ]
    assert len(shadow_events) == 1
    assert shadow_events[0].payload["candidate_executed"] is False
    assert shadow_events[0].payload["decision_event_id"] == observation.decision_event_id

    evaluation_events = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_decision_policy_shadow_evaluated"
    ]
    assert len(evaluation_events) == 1
    assert evaluation_events[0].payload["proposal_id"] == proposal.proposal_id
    assert evaluation_events[0].payload["observations"] == 1
    assert evaluation_events[0].payload["governance_review_ready"] is False
    assert evaluation_events[0].payload["reason"] == "insufficient_shadow_observations"


def test_runtime_consumes_durable_active_policy_as_advisory_guidance(tmp_path: Path):
    runtime, store, planner, _ = make_runtime(tmp_path)

    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-runtime-active",
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective",),
        observed_direction="better",
        decision_events=2,
        observations=2,
        sources=("independent_execution",),
        claim="Repeated empirical observation.",
        created_at="2026-09-29T20:10:00+00:00",
    )
    proposal = DirectorDecisionPolicyEvolution.propose(
        knowledge,
        proposed_change="Use complete when the current mission is materially comparable.",
        created_at="2026-09-29T20:11:00+00:00",
    )
    DirectorDecisionPolicyProposalStore(tmp_path / "tasks.db").record(proposal)
    DirectorDecisionPolicyActiveStore(tmp_path / "tasks.db").activate(
        DirectorDecisionPolicyActive(
            proposal_id=proposal.proposal_id,
            validation_id="dpv-runtime-active",
            authorized_by="director-governance",
            activated_at="2026-09-29T20:12:00+00:00",
        )
    )

    submission = runtime.submit_mission(objective="research Aurora")
    runtime.run_until_terminal(submission)

    messages = [
        item["content"]
        for item in planner.review_contexts[0]
        if "Active Director policy guidance" in item["content"]
    ]
    assert len(messages) == 1
    assert "governed and advisory" in messages[0]
    assert "does not override current evidence" in messages[0]
    assert "Re-evaluate current evidence before deciding" in messages[0]

    contracts = DirectorDecisionPolicyContractStore(tmp_path / "tasks.db").list_all()
    assert len(contracts) == 1
    assert contracts[0].proposal_id == proposal.proposal_id
    assert contracts[0].validation_id == "dpv-runtime-active"
    assert contracts[0].advisory_record()["advisory_only"] is True


def test_runtime_reuses_immutable_active_policy_contract_across_reviews(tmp_path: Path):
    runtime, store, planner, _ = make_runtime(tmp_path, max_continue_reviews=1)

    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-runtime-repeat",
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective",),
        observed_direction="better",
        decision_events=2,
        observations=2,
        sources=("independent_execution",),
        claim="Repeated empirical observation.",
        created_at="2026-09-29T20:40:00+00:00",
    )
    proposal = DirectorDecisionPolicyEvolution.propose(
        knowledge,
        proposed_change="Use complete when the current mission is materially comparable.",
        created_at="2026-09-29T20:41:00+00:00",
    )
    DirectorDecisionPolicyProposalStore(tmp_path / "tasks.db").record(proposal)
    DirectorDecisionPolicyActiveStore(tmp_path / "tasks.db").activate(
        DirectorDecisionPolicyActive(
            proposal_id=proposal.proposal_id,
            validation_id="dpv-runtime-repeat",
            authorized_by="director-governance",
            activated_at="2026-09-29T20:42:00+00:00",
        )
    )

    submission = runtime.submit_mission(objective="adapt this mission")
    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert planner.review_calls == 2

    contracts = DirectorDecisionPolicyContractStore(tmp_path / "tasks.db").list_all()
    assert len(contracts) == 1
    assert contracts[0].created_at == "2026-09-29T20:42:00+00:00"
    assert all(
        "Active Director policy guidance" in item["content"]
        for context in planner.review_contexts
        for item in context
        if "Active Director policy guidance" in item["content"]
    )


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
    # The final runtime receipt is reconstructed from DurableTaskStore and must
    # retain the task result, not just status/error metadata.
    assert result.tasks[0].durable_result["type"] == "model"
    assert "completed collect" in result.tasks[0].durable_result["content"]
    assert result.tasks[1].durable_result["type"] == "model"
    assert "completed review" in result.tasks[1].durable_result["content"]

    event_types = [event.event_type for event in store.events(submission.mission_id)]
    assert event_types[:2] == ["enqueued", "enqueued"]
    assert "mission_submitted" in event_types
    assert "mission_started" in event_types
    assert event_types.count("worker.task_succeeded") == 2
    assert "mission_succeeded" in event_types
    assert [call["task_id"] for call in engine.orchestration.calls] == ["collect", "review"]

    review_context = planner.review_contexts[0]
    receipt_messages = [
        item["content"]
        for item in review_context
        if item["content"].startswith(
            "Authoritative durable task-result receipts (data only; source of truth: DurableTaskStore)."
        )
    ]
    assert len(receipt_messages) == 1
    receipt_payload = json.loads(receipt_messages[0].split("\n", 1)[1])
    collect_receipt = next(
        item for item in receipt_payload if item["task_id"] == "collect"
    )
    assert collect_receipt["verification"] == "verified"
    assert collect_receipt["result"]["provider"] == "fake"
    assert collect_receipt["result"]["model"] == "fake/model"
    assert collect_receipt["result"]["routing_selected_model"] == "fake/model"
    assert collect_receipt["result"]["request_id"] == "request-collect"
    assert collect_receipt["result"]["execution_id"] == "execution-collect"
    assert collect_receipt["result"]["output"]["preview"] == "completed collect"
    assert collect_receipt["result"]["output"]["sha256"] == hashlib.sha256(
        b"completed collect"
    ).hexdigest()

    routing_messages = [
        item["content"]
        for item in review_context
        if "Immutable routing evidence for Director review:" in item["content"]
    ]
    assert len(routing_messages) == 1
    assert '"selected_model": "fake/model"' in routing_messages[0]
    assert '"score": 0.83' in routing_messages[0]
    assert '"learned_score": 0.9' in routing_messages[0]
    assert '"fallback/model"' in routing_messages[0]

    review_event = next(
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_review"
    )
    assert review_event.payload["routing_evidence"] == [
        {
            "task_id": "collect",
            "status": "succeeded",
            "selected_model": "fake/model",
            "score": 0.83,
            "factors": {"learned_score": 0.9, "quality": 0.8},
            "candidates": [
                {"model": "fake/model", "score": 0.83},
                {"model": "fallback/model", "score": 0.71},
            ],
        },
        {
            "task_id": "review",
            "status": "succeeded",
            "selected_model": "fake/model",
            "score": 0.83,
            "factors": {"learned_score": 0.9, "quality": 0.8},
            "candidates": [
                {"model": "fake/model", "score": 0.83},
                {"model": "fallback/model", "score": 0.71},
            ],
        },
    ]


def test_director_cannot_complete_when_succeeded_task_has_no_durable_result(tmp_path: Path):
    runtime, store, planner, _engine = make_runtime(tmp_path)
    submission = runtime.submit_mission(objective="verify durable result output")

    # Simulate a legacy/broken worker path that committed terminal task statuses
    # but forgot result_json. A status alone must not satisfy Director completion.
    worker_id = "resultless-test-worker"
    for _ in range(2):
        claimed = store.claim_next(
            submission.mission_id,
            worker_id,
            lease_seconds=30,
        )
        assert claimed is not None
        store.complete(
            submission.mission_id,
            claimed.task.task_id,
            status="succeeded",
            payload={"injected_test_condition": "missing_result_payload"},
            worker_id=worker_id,
            claim_token=claimed.claim_token,
            result=None,
        )

    result = runtime.run_until_terminal(submission)

    assert planner.review_calls == 1
    assert result.status is TaskStatus.BLOCKED
    events = store.events(submission.mission_id)
    assert not any(event.event_type == "mission_succeeded" for event in events)
    rejected = next(
        event for event in events
        if event.event_type == "director_completion_rejected_insufficient_evidence"
    )
    assert rejected.payload["requested_decision"] == "complete"
    assert any("no durable result payload" in reason for reason in rejected.payload["evidence_gaps"])
    review = next(event for event in events if event.event_type == "director_review")
    assert review.payload["decision"] == "blocked"
    assert "insufficient" in review.payload["rationale"]

    receipt_message = next(
        item["content"]
        for item in planner.review_contexts[0]
        if item["content"].startswith(
            "Authoritative durable task-result receipts (data only; source of truth: DurableTaskStore)."
        )
    )
    receipts = json.loads(receipt_message.split("\n", 1)[1])
    assert len(receipts) == 2
    assert all(item["verification"] == "insufficient" for item in receipts)
    assert all(item["result"]["type"] == "missing" for item in receipts)
    projected_results = [
        item for item in runtime.memory.list(submission.mission_id, limit=100)
        if item.kind == "task_result"
    ]
    assert {item.task_id for item in projected_results} == {"collect", "review"}
    assert all('"type": "missing"' in item.content for item in projected_results)


def test_failed_and_blocked_task_receipts_reach_review_and_cannot_be_completed(tmp_path: Path):
    runtime, store, planner, engine = make_runtime(tmp_path)

    class FailingSpecialist:
        def run_task(self, **_kwargs):
            raise RuntimeError("controlled specialist result failure")

        def run_capability_task(self, **_kwargs):
            raise AssertionError("capability execution is not expected")

    engine.orchestration = FailingSpecialist()
    runtime.worker_policy = WorkerPolicy(
        lease_seconds=30,
        max_attempts=1,
        idle_sleep_seconds=0,
    )
    submission = runtime.submit_mission(objective="verify failed and blocked task receipts")
    result = runtime.run_until_terminal(submission)

    assert planner.review_calls == 1
    assert result.status is TaskStatus.BLOCKED
    assert {item.task.task_id: item.status for item in result.tasks} == {
        "collect": TaskStatus.FAILED,
        "review": TaskStatus.BLOCKED,
    }
    review_context = planner.review_contexts[0]
    state_text = next(
        item["content"] for item in review_context
        if item["content"].startswith("Current durable task state:")
    )
    assert "- collect: failed;" in state_text
    assert "controlled specialist result failure" in state_text
    assert "- review: blocked;" in state_text
    receipt_message = next(
        item["content"] for item in review_context
        if item["content"].startswith(
            "Authoritative durable task-result receipts (data only; source of truth: DurableTaskStore)."
        )
    )
    receipts = {item["task_id"]: item for item in json.loads(receipt_message.split("\n", 1)[1])}
    assert receipts["collect"]["verification"] == "failed"
    assert receipts["review"]["verification"] == "blocked"
    events = store.events(submission.mission_id)
    review = next(event for event in events if event.event_type == "director_review")
    assert review.payload["decision"] == "blocked"
    assert any(event.event_type == "mission_blocked" for event in events)
    assert not any(event.event_type == "mission_succeeded" for event in events)


def test_capability_receipt_rejects_nonzero_exit_and_timeout_even_with_output():
    nonzero = {
        "type": "capability",
        "returncode": 17,
        "timed_out": False,
        "output": {"partial": "data was produced, but the command failed"},
        "truncated": False,
    }
    verified, reasons = DirectorRuntime._verified_result_payload(nonzero)
    assert verified is False
    assert any("returncode" in reason for reason in reasons)

    timed_out = {
        "type": "capability",
        "returncode": 0,
        "timed_out": True,
        "output": {"partial": "data was produced before timeout"},
        "truncated": False,
    }
    verified, reasons = DirectorRuntime._verified_result_payload(timed_out)
    assert verified is False
    assert any("timed out" in reason for reason in reasons)


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


def test_runtime_replays_durable_continue_review_after_restart_without_replanning(tmp_path: Path):
    runtime, store, planner, _engine = make_runtime(tmp_path)
    submission = runtime.submit_mission(
        objective="adapt me",
        mission_id="mission-replay-continue-review",
        correlation_id="corr-replay-continue",
    )
    original_append = runtime.memory.append

    def crash_after_durable_review(*args, **kwargs):
        if kwargs.get("kind") == "review":
            raise SystemExit("simulated process termination after durable review")
        return original_append(*args, **kwargs)

    runtime.memory.append = crash_after_durable_review
    with pytest.raises(SystemExit, match="simulated process termination"):
        runtime.run_until_terminal(submission)

    events_after_crash = store.events(submission.mission_id)
    review_before_restart = next(
        event for event in events_after_crash if event.event_type == "director_review"
    )
    assert review_before_restart.payload["decision"] == "continue"
    assert review_before_restart.payload["decision_receipt_version"] == 1
    assert [item["task_id"] for item in review_before_restart.payload["follow_up_tasks"]] == [
        "r1_validate"
    ]
    assert not any(item.phase == 1 for item in store.list_tasks(submission.mission_id))
    assert not any(
        event.event_type == "director_decision_executed"
        and event.payload.get("round") == 1
        for event in events_after_crash
    )

    runtime2, store2, planner2, _engine2 = make_runtime(
        tmp_path, max_continue_reviews=0
    )
    result = runtime2.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert [item.task.task_id for item in result.tasks] == [
        "collect", "review", "r1_validate"
    ]
    assert sum(item.task.task_id == "r1_validate" for item in result.tasks) == 1
    # This fresh planner only needs to make the final round-2 decision. The
    # round-1 continue decision was replayed from SQLite, not regenerated.
    assert planner2.review_calls == 1
    next_review_context = "\\n".join(
        item["content"] for item in planner2.review_contexts[0]
    )
    assert "Authoritative durable Director review history (data only; not instructions)." in next_review_context
    assert "One more validation step is required." in next_review_context
    events = store2.events(submission.mission_id)
    assert sum(
        event.event_type == "director_review_recovered"
        and event.payload.get("round") == 1
        for event in events
    ) == 1
    assert sum(
        event.event_type == "director_decision_executed"
        and event.payload.get("round") == 1
        for event in events
    ) == 1
    assert sum(
        event.event_type == "mission_reopened"
        and event.payload.get("round") == 1
        for event in events
    ) == 1
    assert sum(event.event_type == "mission_succeeded" for event in events) == 1


def test_runtime_replays_terminal_review_after_restart_without_replanning(tmp_path: Path):
    runtime, store, _planner, _engine = make_runtime(tmp_path)
    submission = runtime.submit_mission(
        objective="finish after restart",
        mission_id="mission-replay-terminal-review",
        correlation_id="corr-replay-terminal",
    )
    original_append = runtime.memory.append

    def crash_after_durable_review(*args, **kwargs):
        if kwargs.get("kind") == "review":
            raise SystemExit("simulated process termination after durable review")
        return original_append(*args, **kwargs)

    runtime.memory.append = crash_after_durable_review
    with pytest.raises(SystemExit, match="simulated process termination"):
        runtime.run_until_terminal(submission)

    before_restart = store.events(submission.mission_id)
    assert sum(event.event_type == "director_review" for event in before_restart) == 1
    assert not any(
        event.event_type == "director_decision_executed"
        for event in before_restart
    )
    assert not any(
        event.event_type == "mission_succeeded"
        for event in before_restart
    )

    runtime2, store2, planner2, _engine2 = make_runtime(tmp_path)
    result = runtime2.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert planner2.review_calls == 0
    events = store2.events(submission.mission_id)
    assert sum(event.event_type == "director_review" for event in events) == 1
    assert sum(event.event_type == "director_decision_executed" for event in events) == 1
    assert sum(event.event_type == "mission_succeeded" for event in events) == 1


def test_runtime_reuses_review_round_when_process_stops_before_decision_receipt(tmp_path: Path):
    runtime, store, planner, _engine = make_runtime(tmp_path)
    submission = runtime.submit_mission(
        objective="adapt after an interrupted review",
        mission_id="mission-reuse-review-round",
        correlation_id="corr-reuse-review-round",
    )

    def crash_during_model_review(**_kwargs):
        raise SystemExit("simulated process termination before review receipt")

    planner.review = crash_during_model_review
    with pytest.raises(SystemExit, match="before review receipt"):
        runtime.run_until_terminal(submission)

    before_restart = store.events(submission.mission_id)
    started = [
        event for event in before_restart
        if event.event_type == "director_review_started"
    ]
    assert [event.payload["round"] for event in started] == [1]
    assert not any(event.event_type == "director_review" for event in before_restart)

    runtime2, store2, planner2, _engine2 = make_runtime(tmp_path)
    result = runtime2.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert planner2.review_calls == 2
    events = store2.events(submission.mission_id)
    started_rounds = [
        event.payload["round"] for event in events
        if event.event_type == "director_review_started"
    ]
    assert started_rounds == [1, 1, 2]
    assert sum(event.event_type == "mission_succeeded" for event in events) == 1


def test_runtime_does_not_ack_skill_event_when_memory_projection_fails(tmp_path: Path):
    from aurora.orchestration.mission_memory import MissionMemoryError

    runtime, store, _planner, _engine = make_runtime(tmp_path)
    event = store.append_event(
        mission_id="mission-event-retry",
        task_id="task-skill",
        event_type="skill.evaluated",
        payload={"skill_id": "research", "score": 0.9},
    )
    real_append = runtime.memory.append

    def fail_once(**kwargs):
        if kwargs.get("event_id") == event.event_id:
            raise MissionMemoryError("temporary mailbox outage")
        return real_append(**kwargs)

    runtime.memory.append = fail_once
    runtime.run_once()

    cursor_id = runtime._subscription.cursor_id
    assert store.get_event_cursor(cursor_id) < event.event_id
    assert [item.event_id for item in runtime.event_bus.poll(runtime._subscription)] == [
        event.event_id
    ]

    runtime.memory.append = real_append
    runtime.run_once()

    assert store.get_event_cursor(cursor_id) == event.event_id
    projected = [
        item for item in runtime.memory.list("mission-event-retry")
        if item.event_id == event.event_id
    ]
    assert len(projected) == 1


def test_runtime_reconciles_task_result_after_mailbox_write_failure(tmp_path: Path):
    from aurora.orchestration.mission_memory import MissionMemoryError

    runtime, _store, _planner, _engine = make_runtime(tmp_path)
    real_append = runtime.memory.append
    failed_once = {"value": False}

    def fail_one_result(**kwargs):
        if kwargs.get("kind") == "task_result" and not failed_once["value"]:
            failed_once["value"] = True
            raise MissionMemoryError("temporary task-result projection outage")
        return real_append(**kwargs)

    runtime.memory.append = fail_one_result
    submission = runtime.submit_mission(objective="recover missing task results")
    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert failed_once["value"] is True
    task_messages = [
        message for message in runtime.memory.list(submission.mission_id, limit=500)
        if message.kind == "task_result"
    ]
    ids = [message.metadata.get("task_id") for message in task_messages]
    assert set(ids) == {"collect", "review"}
    assert len(ids) == len(set(ids))


def test_runtime_stop_event_allows_clean_24x7_shutdown(tmp_path: Path):
    runtime, _, _, _ = make_runtime(tmp_path)
    stop = Event()
    stop.set()
    runtime.run_forever(stop)


def test_adaptive_director_replans_after_terminal_phase(tmp_path: Path):
    runtime, store, planner, engine = make_runtime(tmp_path)
    submission = runtime.submit_mission(objective="adapt this mission")
    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert planner.review_calls == 2
    task_ids = [item.task.task_id for item in result.tasks]
    assert task_ids == ["collect", "review", "r1_validate"]
    assert result.tasks[-1].status is TaskStatus.SUCCEEDED
    context = runtime.memory.context(submission.mission_id, limit=20)
    assert any("completed collect" in item["content"] for item in context)
    assert any("Decision=continue" in item["content"] for item in context)

    events = [event.event_type for event in store.events(submission.mission_id)]
    assert "director_review" in events
    assert events.count("director_decision_executed") == 2
    assert "director_decision_evaluated" in events
    assert "mission_reopened" in events
    assert events.count("mission_succeeded") == 1
    assert [call["task_id"] for call in engine.orchestration.calls] == [
        "collect",
        "review",
        "r1_validate",
    ]

    evaluations = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_decision_evaluated"
    ]
    assert len(evaluations) == 2
    assert evaluations[0].payload["decision"] == "continue"
    assert evaluations[0].payload["score"] == 1.0
    assert evaluations[0].payload["outcome"] == "follow_up_succeeded"
    assert evaluations[0].payload["quality"] == "supported_success"
    assert evaluations[0].payload["evidence_state"] == "outcome_observed"
    assert evaluations[0].payload["attribution"] == "temporal_outcome_alignment"
    assert evaluations[0].payload["round"] == 1
    assert evaluations[1].payload["decision"] == "complete"
    assert evaluations[1].payload["score"] == 1.0
    assert evaluations[1].payload["outcome"] == "mission_completed"
    assert evaluations[1].payload["quality"] == "supported_success"
    assert evaluations[1].payload["evidence_state"] == "outcome_observed"
    assert evaluations[1].payload["attribution"] == "temporal_outcome_alignment"
    assert evaluations[1].payload["round"] == 2

    second_review_context = planner.review_contexts[1]
    learning_messages = [
        item["content"]
        for item in second_review_context
        if "Durable Director decision learning evidence:" in item["content"]
    ]
    assert len(learning_messages) == 1
    assert '"decision": "continue"' in learning_messages[0]
    assert '"score": 1.0' in learning_messages[0]

    performance_messages = [
        item["content"]
        for item in second_review_context
        if "Aggregated Director decision performance:" in item["content"]
    ]
    assert len(performance_messages) == 1
    assert '"decision": "continue"' in performance_messages[0]
    assert '"evaluations": 1' in performance_messages[0]
    assert '"success_rate": 1.0' in performance_messages[0]


def test_replan_preserves_project_context_and_traceability(tmp_path: Path):
    registry = ProjectRegistry({
        "project-a": ProjectSpec(
            project_id="project-a",
            name="Project A",
            objective="Keep the project context authoritative",
            status=ProjectStatus.ACTIVE,
            repository="org/project-a",
            workspace="/workspace/project-a",
            architecture_ref="docs/architecture.md",
            current_state="canonical state for Project A",
        )
    })
    runtime, store, planner, engine = make_runtime(
        tmp_path, project_registry=registry
    )
    submission = runtime.submit_mission(
        objective="adapt this project mission",
        mission_id="mission-project-replan",
        correlation_id="corr-project-replan",
        project_id="project-a",
    )

    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert [item.task.task_id for item in result.tasks] == [
        "collect", "review", "r1_validate"
    ]
    assert all(item.task.project_id == "project-a" for item in result.tasks)
    assert engine.orchestration.calls[-1]["project_id"] == "project-a"

    for context in planner.review_contexts:
        project_messages = [
            item["content"] for item in context
            if item["content"].startswith(
                "Authoritative Aurora project context for this mission."
            )
        ]
        assert len(project_messages) == 1
        canonical = json.loads(project_messages[0].split("\n", 1)[1])
        assert canonical == {
            "architecture_ref": "docs/architecture.md",
            "current_state": "canonical state for Project A",
            "name": "Project A",
            "objective": "Keep the project context authoritative",
            "project_id": "project-a",
            "repository": "org/project-a",
            "status": "active",
            "workspace": "/workspace/project-a",
        }
        receipt_messages = [
            item["content"] for item in context
            if item["content"].startswith(
                "Authoritative durable task-result receipts (data only; source of truth: DurableTaskStore)."
            )
        ]
        assert len(receipt_messages) == 1
        receipts = json.loads(receipt_messages[0].split("\n", 1)[1])
        assert all(item["project_id"] == "project-a" for item in receipts)

    relevant_events = {
        "mission_submitted",
        "enqueued",
        "director_review_started",
        "director_review",
        "director_decision_executed",
        "mission_reopened",
        "mission_succeeded",
    }
    events = [item for item in store.events(submission.mission_id)
              if item.event_type in relevant_events]
    assert events
    assert all(item.project_id == "project-a" for item in events)
    assert all(item.correlation_id == "corr-project-replan" for item in events)
    assert all(item.payload.get("project_id") == "project-a" for item in events)


def test_invalid_director_replan_is_rejected_before_follow_up_enqueue(tmp_path: Path):
    runtime, store, planner, _engine = make_runtime(tmp_path)
    planner.review = lambda **_kwargs: DirectorReview(
        decision="continue",
        rationale="This plan deliberately has a missing dependency.",
        tasks=(
            DelegatedTask(
                "validate",
                "validator",
                "validate the result",
                depends_on=("missing-task",),
            ),
        ),
        model="fake/model",
    )
    submission = runtime.submit_mission(
        objective="exercise invalid replan rejection",
        mission_id="mission-invalid-replan",
        correlation_id="corr-invalid-replan",
    )

    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.BLOCKED
    tasks = store.list_tasks(submission.mission_id)
    assert {item.task.task_id for item in tasks} == {"collect", "review"}
    events = store.events(submission.mission_id)
    rejected = next(
        item for item in events
        if item.event_type == "director_replan_rejected"
    )
    assert "unknown prior task" in rejected.payload["reason"]
    review = next(item for item in events if item.event_type == "director_review")
    assert review.payload["decision"] == "blocked"
    assert review.project_id is None
    assert any(item.event_type == "mission_blocked" for item in events)


def test_comparative_evidence_is_advisory_in_next_director_cycle(tmp_path: Path):

    runtime, store, planner, _ = make_runtime(tmp_path)
    submission = runtime.submit_mission(objective="adapt compare safely")

    for _ in range(50):
        runtime.run_once()
        decision_events = [
            event for event in store.events(submission.mission_id)
            if event.event_type == "director_decision_executed"
        ]
        if decision_events:
            break
    assert decision_events
    review_events = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "director_review"
    ]
    assert review_events
    # Comparative evidence is anchored to the Director decision (review) event,
    # not to its execution event. The runtime uses the same identity when
    # attaching advisory evidence to the next Director cycle.
    decision_event_id = int(review_events[-1].event_id)

    runtime.record_decision_comparison(
        mission_id=submission.mission_id,
        decision_event_id=decision_event_id,
        chosen_decision="continue",
        alternative="complete",
        comparative_evidence=DirectorDecisionComparativeEvidence(
            source="independent_execution",
            alternative_execution_id="alternative-execution-1",
            chosen_execution_id=f"chosen-execution-{decision_event_id}",
            chosen_outcome="follow_up_succeeded",
            alternative_outcome="mission_succeeded",
            comparability_basis=("same_objective", "same_environment_class"),
            observed_direction="better",
        ),
        created_at="2026-09-29T15:45:00+00:00",
    )

    runtime.run_until_terminal(submission)

    advisory_messages = [
        item["content"]
        for context in planner.review_contexts
        for item in context
        if "Comparative evidence advisory for this Director decision." in item["content"]
    ]
    assert len(advisory_messages) >= 1
    assert '"state": "potentially_better_observed"' in advisory_messages[0]
    assert '"causal_attribution": "unsupported"' in advisory_messages[0]


def test_comparative_evidence_aggregate_is_advisory(tmp_path: Path):
    runtime, store, planner, _ = make_runtime(tmp_path)
    submission = runtime.submit_mission(objective="adapt compare safely")

    for _ in range(50):
        runtime.run_once()
        review_events = [
            event for event in store.events(submission.mission_id)
            if event.event_type == "director_review"
        ]
        if review_events:
            break
    assert review_events
    decision_event_id = int(review_events[-1].event_id)

    for index in range(1, 3):
        runtime.record_decision_comparison(
            mission_id=submission.mission_id,
            decision_event_id=decision_event_id,
            chosen_decision="continue",
            alternative="complete",
            comparative_evidence=DirectorDecisionComparativeEvidence(
                source="independent_execution",
                alternative_execution_id=f"alternative-execution-{index}",
                chosen_execution_id=f"chosen-execution-{index}",
                chosen_outcome="follow_up_succeeded",
                alternative_outcome="mission_succeeded",
                comparability_basis=("same_objective", "same_environment_class"),
                observed_direction="better",
            ),
            created_at=f"2026-09-29T15:4{index}:00+00:00",
        )

    runtime.run_until_terminal(submission)

    aggregate_messages = [
        item["content"]
        for context in planner.review_contexts
        for item in context
        if "Aggregated comparative observations for this Director decision." in item["content"]
    ]
    assert aggregate_messages
    assert '"observations": 2' in aggregate_messages[0]
    assert '"better_observations": 2' in aggregate_messages[0]
    assert '"consistent_direction": "better"' in aggregate_messages[0]
    assert "causality" in aggregate_messages[0]


def test_cross_decision_replication_is_advisory(tmp_path: Path):
    runtime, store, planner, _ = make_runtime(tmp_path, max_continue_reviews=2)
    submission = runtime.submit_mission(objective="adapt compare safely")

    def reviews():
        return [
            event
            for event in store.events(submission.mission_id)
            if event.event_type == "director_review"
        ]

    # Record the first comparison before the second Director cycle.
    for _ in range(100):
        runtime.run_once()
        if len(reviews()) >= 1:
            break
    assert len(reviews()) >= 1
    first_id = int(reviews()[0].event_id)
    runtime.record_decision_comparison(
        mission_id=submission.mission_id,
        decision_event_id=first_id,
        chosen_decision="continue",
        alternative="complete",
        comparative_evidence=DirectorDecisionComparativeEvidence(
            source="independent_execution",
            alternative_execution_id="alternative-replication-a",
            chosen_execution_id="chosen-replication-a",
            chosen_outcome="follow_up_succeeded",
            alternative_outcome="mission_succeeded",
            comparability_basis=("same_objective", "same_environment_class"),
            observed_direction="better",
        ),
        created_at="2026-09-29T16:21:00+00:00",
    )

    # Record an independent comparison for the second decision before the
    # third Director cycle, where cross-decision replication can be observed.
    for _ in range(100):
        runtime.run_once()
        if len(reviews()) >= 2:
            break
    assert len(reviews()) >= 2
    second_id = int(reviews()[1].event_id)
    runtime.record_decision_comparison(
        mission_id=submission.mission_id,
        decision_event_id=second_id,
        chosen_decision="continue",
        alternative="complete",
        comparative_evidence=DirectorDecisionComparativeEvidence(
            source="independent_execution",
            alternative_execution_id="alternative-replication-b",
            chosen_execution_id="chosen-replication-b",
            chosen_outcome="follow_up_succeeded",
            alternative_outcome="mission_succeeded",
            comparability_basis=("same_objective", "same_environment_class"),
            observed_direction="better",
        ),
        created_at="2026-09-29T16:22:00+00:00",
    )

    # The third review is the actual consumer of the replicated evidence.
    for _ in range(100):
        runtime.run_once()
        if len(reviews()) >= 3:
            break
    assert len(reviews()) >= 3

    replication_messages = [
        item["content"]
        for context in planner.review_contexts
        for item in context
        if "Cross-decision comparative replication advisory." in item["content"]
    ]
    assert replication_messages
    assert '"decision_events": 2' in replication_messages[-1]
    assert '"observations": 2' in replication_messages[-1]
    assert '"consistent_direction": "better"' in replication_messages[-1]
    assert "causality" in replication_messages[-1]

    knowledge_messages = [
        item["content"]
        for context in planner.review_contexts
        for item in context
        if "Promoted empirical Director knowledge advisory." in item["content"]
    ]
    assert knowledge_messages
    assert '"observed_direction": "better"' in knowledge_messages[-1]
    assert '"state": "promoted_empirical"' in knowledge_messages[-1]


def test_runtime_submission_stays_accepted_when_ownership_telemetry_fails(tmp_path: Path):
    runtime, store, _planner, _engine = make_runtime(tmp_path)

    def unavailable_telemetry(**kwargs):
        raise RuntimeError("event bus temporarily unavailable")

    runtime.event_bus.publish = unavailable_telemetry
    submission = runtime.submit_mission(objective="accepted despite telemetry outage")

    assert submission.mission_id
    assert len(store.list_tasks(submission.mission_id)) == 2
    submitted = [
        event for event in store.events(submission.mission_id)
        if event.event_type == "mission_submitted"
    ]
    assert len(submitted) == 1
    assert submitted[0].payload["objective"] == "accepted despite telemetry outage"
    assert runtime.memory.first_of_kind(submission.mission_id, "mission_objective") is not None
    assert runtime.memory.first_of_kind(submission.mission_id, "mission_plan") is not None


def test_runtime_reconciles_partial_mailbox_write_from_durable_submission(tmp_path: Path):
    from aurora.orchestration.mission_memory import MissionMemoryError

    runtime, store, _planner, _engine = make_runtime(tmp_path)
    real_append = runtime.memory.append
    failed_once = {"value": False}

    def fail_plan_once(**kwargs):
        if kwargs.get("kind") == "mission_plan" and not failed_once["value"]:
            failed_once["value"] = True
            raise MissionMemoryError("temporary mailbox outage")
        return real_append(**kwargs)

    runtime.memory.append = fail_plan_once
    submission = runtime.submit_mission(objective="recover mailbox projection")

    # Queue acceptance is authoritative even if the auxiliary mailbox is down.
    assert len(store.list_tasks(submission.mission_id)) == 2
    assert runtime.memory.first_of_kind(submission.mission_id, "mission_objective") is not None
    assert runtime.memory.first_of_kind(submission.mission_id, "mission_plan") is None

    runtime.memory.append = real_append
    runtime.run_once()

    assert runtime.memory.first_of_kind(submission.mission_id, "mission_objective") is not None
    assert runtime.memory.first_of_kind(submission.mission_id, "mission_plan") is not None
    assert len([
        message for message in runtime.memory.list(submission.mission_id)
        if message.kind == "mission_objective"
    ]) == 1
    assert len([
        message for message in runtime.memory.list(submission.mission_id)
        if message.kind == "mission_plan"
    ]) == 1

def test_retry_attempt_does_not_mask_the_final_task_result_in_shared_memory(tmp_path: Path):
    """A retry is an attempt record; the final success must remain projectable."""
    runtime, store, _planner, engine = make_runtime(tmp_path)
    attempts = {"collect": 0}

    class FailFirstAttempt:
        def run_task(self, **kwargs):
            task_id = kwargs["task_id"]
            attempts[task_id] = attempts.get(task_id, 0) + 1
            if task_id == "collect" and attempts[task_id] == 1:
                raise RuntimeError("controlled transient failure")

            return TaskResult(
                task_id=task_id,
                role=kwargs["role"],
                response=InferenceResponse(
                    model="fake/model",
                    provider="fake",
                    content=f"completed {task_id}",
                    request_id=f"request-{task_id}",
                    execution_id=f"execution-{task_id}",
                    requested_model="fake/model",
                    selected_model="fake/model",
                    provider_model="fake/model",
                    reported_model="fake/model",
                ),
            )

        def run_capability_task(self, **kwargs):
            raise AssertionError("capability execution is not expected")

    # Drive the same TaskEngine through its canonical durable worker path.
    engine.orchestration = FailFirstAttempt()
    submission = runtime.submit_mission(objective="recover a transient task failure")
    result = runtime.run_until_terminal(submission)

    assert result.status is TaskStatus.SUCCEEDED
    assert attempts["collect"] == 2
    stored = store.get(submission.mission_id, "collect")
    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.attempts == 2

    messages = runtime.memory.list(submission.mission_id, limit=100)
    collect_attempts = [
        item for item in messages
        if item.task_id == "collect" and item.kind == "task_attempt"
    ]
    collect_results = [
        item for item in messages
        if item.task_id == "collect" and item.kind == "task_result"
    ]

    assert len(collect_attempts) == 1
    assert collect_attempts[0].metadata["action"] == "retried"
    assert collect_attempts[0].metadata["attempt"] == 1
    assert collect_attempts[0].metadata["status"] == "failed"
    assert collect_attempts[0].metadata["error"] == "controlled transient failure"
    assert "controlled transient failure" in collect_attempts[0].content
    assert len(collect_results) == 1
    assert collect_results[0].metadata["action"] == "succeeded"
    assert collect_results[0].metadata["status"] == "succeeded"
    assert collect_results[0].metadata["attempt"] == 2
    assert "completed collect" in collect_results[0].content
    assert "collect" in runtime.memory.task_result_task_ids(submission.mission_id)


def test_run_forever_recovers_after_transient_cycle_error_without_losing_mission(tmp_path: Path):
    runtime, store, _planner, _engine = make_runtime(tmp_path)
    submission = runtime.submit_mission(objective="finish despite one transient runtime cycle error")
    real_run_once = runtime.run_once
    stop = Event()
    calls = {"count": 0}

    def fail_first_cycle_then_resume():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("controlled transient event-store failure")
        cycle = real_run_once()
        if runtime._is_finalized(submission.mission_id):
            stop.set()
        return cycle

    runtime.run_once = fail_first_cycle_then_resume
    runtime._refresh_model_catalogs_if_due = lambda: None
    runtime.run_forever(stop)

    assert calls["count"] >= 2
    assert runtime._is_finalized(submission.mission_id)
    assert runtime._final_status(submission.mission_id) is TaskStatus.SUCCEEDED
    assert len(store.list_tasks(submission.mission_id)) == 2
    failures = [
        event for event in store.events("system")
        if event.event_type == "director_runtime_cycle_failed"
    ]
    assert len(failures) == 1
    assert failures[0].payload["consecutive_failures"] == 1
    assert failures[0].payload["error_type"] == "RuntimeError"


def test_conversation_mission_idempotency_preserves_one_durable_order_and_correlation(tmp_path: Path):
    runtime, store, planner, _engine = make_runtime(tmp_path)
    mission_id = "mission-conversation-turn-correlation-test"
    correlation_id = "corr-conversation-turn-correlation-test"
    objective = "process this conversation order exactly once"

    submission = runtime.submit_mission(
        objective=objective,
        mission_id=mission_id,
        correlation_id=correlation_id,
    )
    replay = runtime.submit_mission(
        objective=objective,
        mission_id=mission_id,
        correlation_id=correlation_id,
    )

    assert replay.mission_id == submission.mission_id == mission_id
    assert len(planner.calls) == 1
    assert len(store.list_tasks(mission_id)) == 2
    events_before_execution = store.events(mission_id)
    assert len([
        event for event in events_before_execution
        if event.event_type == "mission_submitted"
    ]) == 1
    assert all(
        event.correlation_id == correlation_id
        for event in events_before_execution
        if event.event_type in {"enqueued", "mission_submitted", "team.formed"}
    )

    result = runtime.run_until_terminal(submission)
    assert result.status is TaskStatus.SUCCEEDED

    events = store.events(mission_id)
    required_types = {
        "mission_submitted",
        "director_ownership_acquired",
        "mission_started",
        "worker.task_succeeded",
        "mission_succeeded",
    }
    for event_type in required_types:
        matching = [event for event in events if event.event_type == event_type]
        assert matching, f"missing lifecycle event: {event_type}"
        assert all(event.correlation_id == correlation_id for event in matching), event_type

    messages = runtime.memory.list(mission_id, limit=100)
    for kind in ("mission_objective", "mission_plan", "task_result"):
        matching = [message for message in messages if message.kind == kind]
        assert matching, f"missing mailbox projection: {kind}"
        assert all(message.correlation_id == correlation_id for message in matching), kind


def test_conversation_wait_timeout_preserves_accepted_durable_mission_receipt(tmp_path: Path):
    from aurora.orchestration.director_runtime import DirectorMissionWaitTimeout

    runtime, store, planner, _engine = make_runtime(tmp_path)
    submission = runtime.submit_mission(
        objective="complete after the conversation wait deadline",
        mission_id="mission-timeout-receipt-test",
        correlation_id="corr-timeout-receipt-test",
    )

    try:
        runtime.wait_until_terminal(
            submission,
            timeout_seconds=0.001,
            poll_interval_seconds=0.001,
        )
    except DirectorMissionWaitTimeout as exc:
        receipt = exc.result
    else:
        raise AssertionError("non-terminal mission should return a timeout receipt")

    assert receipt.submission.mission_id == submission.mission_id
    assert receipt.status is TaskStatus.RUNNING
    assert receipt.wait_timed_out is True
    assert receipt.wait_timeout_seconds == 0.001
    assert all(item.status in {TaskStatus.PENDING, TaskStatus.RUNNING} for item in receipt.tasks)
    assert len(store.list_tasks(submission.mission_id)) == 2
    assert len([
        event for event in store.events(submission.mission_id)
        if event.event_type == "mission_submitted"
    ]) == 1

    # The caller's deadline must not cancel or mark the durable mission failed.
    result = runtime.run_until_terminal(submission)
    assert result.status is TaskStatus.SUCCEEDED
    assert len(store.list_tasks(submission.mission_id)) == 2
    assert len(planner.calls) == 1
