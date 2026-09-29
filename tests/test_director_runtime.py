from pathlib import Path
from threading import Event

from aurora.inference.models import InferenceResponse
from aurora.orchestration.director import DirectorPlan, DirectorReview
from aurora.orchestration.director_decision_comparison import DirectorDecisionComparativeEvidence
from aurora.orchestration.director_runtime import DirectorRuntime
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


class FakePlanner:
    def __init__(self, *, max_continue_reviews: int = 1):
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
                selected_model="fake/model",
                routing_score=0.83,
                routing_factors={"learned_score": 0.9, "quality": 0.8},
                routing_candidates=(("fake/model", 0.83), ("fallback/model", 0.71)),
            ),
        )

    def run_capability_task(self, **kwargs):
        raise AssertionError("capability execution is not expected")


def make_runtime(tmp_path: Path, *, max_continue_reviews: int = 1):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    planner = FakePlanner(max_continue_reviews=max_continue_reviews)
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

    review_context = planner.review_contexts[0]
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

