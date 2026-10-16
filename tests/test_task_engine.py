from pathlib import Path
from types import SimpleNamespace

import pytest

from aurora.autonomy.capability_runtime import CapabilityExecutionResult
from aurora.autonomy.models import CapabilityResult
from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import CapabilityTaskResult, TaskResult
from aurora.orchestration.task_engine import (
    DelegatedTask,
    TaskEngine,
    TaskEngineError,
    TaskStatus,
    ResearchTaskResult,
)


class FakeRouter(ModelRouter):
    def generate(self, request, capability=None):
        return InferenceResponse(
            model="fake/model",
            provider="fake",
            content=f"done:{request.metadata['task_id']}",
        )


class FakeCapabilityRuntime:
    def __init__(self):
        self.calls = []

    def execute(self, request, *, workspace, args=()):
        self.calls.append((request, Path(workspace), tuple(args)))
        return CapabilityExecutionResult(
            capability=CapabilityResult(
                name=request.name,
                status="installed",
                source="pypi",
                verification="verified",
                path=str(workspace / "managed-site"),
                version="2.31.0",
            ),
            command=("/workspace/managed-site/bin/python", *tuple(args)),
            returncode=0,
            stdout="2.31.0\n",
            stderr="",
        )


def engine():
    from aurora.orchestration.engine import OrchestrationEngine

    return OrchestrationEngine(
        FakeRouter([ModelSpec("fake/model", "fake", "https://example.invalid")]),
        FakeCapabilityRuntime(),
    )


def test_task_engine_orders_dependencies_before_dependents(tmp_path):
    tasks = [
        DelegatedTask("research", "researcher", "collect evidence"),
        DelegatedTask(
            "inspect",
            "analyst",
            "inspect acquired data",
            capability="python.package:requests",
            args=("-c", "print('ok')"),
            depends_on=("research",),
        ),
    ]

    result = TaskEngine(engine(), tmp_path).execute(tasks)

    assert [item.task.task_id for item in result] == ["research", "inspect"]
    assert all(item.status is TaskStatus.SUCCEEDED for item in result)
    assert isinstance(result[0].model_result, TaskResult)
    assert isinstance(result[1].capability_result, CapabilityTaskResult)


def test_task_engine_blocks_failed_dependency(tmp_path):
    class FailingEngine:
        def run_task(self, **kwargs):
            raise RuntimeError("planner failure")

        def run_capability_task(self, **kwargs):
            raise AssertionError("blocked dependency must not execute")

    tasks = [
        DelegatedTask("a", "agent", "fail"),
        DelegatedTask(
            "b", "agent", "use result",
            capability="tool:rg",
            depends_on=("a",),
        ),
    ]

    result = TaskEngine(FailingEngine(), tmp_path).execute(tasks)
    assert result[0].status is TaskStatus.FAILED
    assert result[1].status is TaskStatus.BLOCKED
    assert result[1].error == "dependency did not succeed"


def test_task_engine_rejects_unknown_dependency(tmp_path):
    with pytest.raises(TaskEngineError, match="unknown task"):
        TaskEngine(engine(), tmp_path).validate(
            [DelegatedTask("a", "agent", "work", depends_on=("missing",))]
        )


def test_task_engine_rejects_cycles(tmp_path):
    with pytest.raises(TaskEngineError, match="cycle"):
        TaskEngine(engine(), tmp_path).validate(
            [
                DelegatedTask("a", "agent", "a", depends_on=("b",)),
                DelegatedTask("b", "agent", "b", depends_on=("a",)),
            ]
        )


def test_task_engine_requires_task_identity_and_objective(tmp_path):
    with pytest.raises(TaskEngineError, match="empty role or objective"):
        TaskEngine(engine(), tmp_path).validate(
            [DelegatedTask("task", "", "work")]
        )


def test_task_engine_runs_independent_ready_nodes_in_parallel(tmp_path):
    from threading import Lock
    from time import sleep

    class ParallelEngine:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.lock = Lock()

        def run_task(self, **kwargs):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            sleep(0.05)
            with self.lock:
                self.active -= 1
            return TaskResult(
                task_id=kwargs["task_id"],
                role=kwargs["role"],
                response=InferenceResponse(
                    model="fake/model",
                    provider="fake",
                    content="ok",
                ),
            )

        def run_capability_task(self, **kwargs):
            raise AssertionError("not expected")

    runtime = ParallelEngine()
    tasks = [
        DelegatedTask("a", "agent", "parallel A"),
        DelegatedTask("b", "agent", "parallel B"),
        DelegatedTask("c", "agent", "join", depends_on=("a", "b")),
    ]

    result = TaskEngine(runtime, tmp_path).execute(tasks)

    assert runtime.peak == 2
    assert [item.task.task_id for item in result[:2]] == ["a", "b"]
    assert result[2].task.task_id == "c"
    assert all(item.status is TaskStatus.SUCCEEDED for item in result)


def test_research_result_is_not_learning_authorized_until_trusted(tmp_path):
    from aurora.research.models import EvidenceState

    pending = ResearchTaskResult(
        request_id="research-1",
        source_ids=("src-1",),
        claim_ids=("claim-1",),
    )
    assert pending.trust_state is EvidenceState.INSUFFICIENT
    assert pending.learning_authorized is False

    trusted = ResearchTaskResult(
        request_id="research-1",
        source_ids=("src-1",),
        claim_ids=("claim-1",),
        trust_state=EvidenceState.VERIFIED,
        knowledge_artifact_ids=("kart-1",),
    )
    assert trusted.learning_authorized is True


class FakeResearchLearningGate:
    def authorize(self, artifact_ids):
        from aurora.research.learning import ResearchLearningSignal
        return ResearchLearningSignal(tuple(artifact_ids))


def test_research_task_accepts_verified_artifact_references(tmp_path):
    from aurora.research.models import ClaimRecord, SourceRecord, EvidenceState

    class FakeResearchService:
        def retrieve_and_extract(self, request):
            return (
                (SourceRecord(
                    source_id="src-1",
                    url="https://example.test/source",
                    retrieved_at="2026-09-28T00:00:00+00:00",
                    content_sha256="sha-1",
                    status_code=200,
                    content_text="evidence",
                ),),
                (ClaimRecord(
                    claim_id="claim-1",
                    source_id="src-1",
                    source_content_sha256="sha-1",
                    claim="evidence",
                    start_offset=0,
                    end_offset=8,
                    extraction_confidence=1.0,
                ),),
            )

    task_engine = TaskEngine(
        engine(),
        tmp_path / "workspace",
        research_service=FakeResearchService(),
        research_learning_gate=FakeResearchLearningGate(),
    )
    task = DelegatedTask(
        "research-1",
        "researcher",
        "collect evidence",
        capability="research",
        args=("url:https://example.test/source", "artifact:kart-1"),
    )

    result = task_engine.execute_one(task)
    research = result.capability_result

    assert research.trust_state is EvidenceState.VERIFIED
    assert research.knowledge_artifact_ids == ("kart-1",)
    assert research.learning_authorized is True


def test_task_engine_delegates_governed_research(tmp_path):
    from aurora.research.models import ClaimRecord, SourceRecord

    class FakeResearchService:
        def retrieve_and_extract(self, request):
            source = SourceRecord(
                source_id="src-1",
                url="https://example.test/source",
                retrieved_at="2026-09-28T00:00:00+00:00",
                content_sha256="sha-1",
                status_code=200,
                content_text="Aurora evidence.",
            )
            claim = ClaimRecord(
                claim_id="claim-1",
                source_id="src-1",
                source_content_sha256="sha-1",
                claim="Aurora evidence.",
                start_offset=0,
                end_offset=16,
                extraction_confidence=1.0,
            )
            assert request.objective == "investigate Aurora"
            return (source,), (claim,)

        def discover(self, request):
            raise AssertionError("explicit sources should not require discovery")

    result = TaskEngine(
        engine(),
        tmp_path,
        research_service=FakeResearchService(),
    ).execute(
        [
            DelegatedTask(
                "research-1",
                "researcher",
                "investigate Aurora",
                capability="research",
                args=("https://example.test/source",),
            )
        ]
    )

    assert result[0].status is TaskStatus.SUCCEEDED
    research = result[0].capability_result
    assert research.request_id == "research-research-1"
    assert research.source_ids == ("src-1",)
    assert research.claim_ids == ("claim-1",)


def test_untrusted_research_reference_fails_closed_before_model_execution(tmp_path):
    from aurora.inference.model_performance import ModelPerformanceStore
    from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
    from aurora.research.learning import ResearchLearningGate, ResearchLearningError
    from aurora.research.store import ResearchStore
    from aurora.skills import SkillRegistry
    from aurora.skills.models import SkillSpec

    research_store = ResearchStore(tmp_path / "research.db")
    gate = ResearchLearningGate(research_store)
    skills = SkillRegistry({
        "research": SkillSpec(
            "research",
            "1.0.0",
            "Research specialist",
            "Research using verified evidence.",
            "Use verified evidence.",
            frozenset(),
            ("source_check",),
            ("citation_check",),
            ("research",),
        )
    })
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher", "researcher", "research", "Use evidence.",
                frozenset(), (), ("research",),
            )
        },
        departments={
            "research": DepartmentSpec(
                "research", "Research", ("researcher",), (), frozenset(),
            )
        },
    )

    class MustNotExecute:
        def run_task(self, **kwargs):
            raise AssertionError("untrusted research must not reach model execution")

        def run_capability_task(self, **kwargs):
            raise AssertionError("untrusted research must not reach capability execution")

    task_engine = TaskEngine(
        MustNotExecute(),
        tmp_path / "workspace",
        organization=organization,
        skill_registry=skills,
        skill_learning=__import__("aurora.skills.learning", fromlist=["SkillLearningStore"]).SkillLearningStore(tmp_path / "skills.db"),
        model_performance=ModelPerformanceStore(tmp_path / "model-performance.db"),
        research_learning_gate=gate,
    )

    with pytest.raises(ResearchLearningError, match="unknown knowledge artifact"):
        task_engine.execute_one(
            DelegatedTask(
                "blocked-untrusted",
                "researcher",
                "analyze untrusted research",
                args=("artifact:does-not-exist",),
                skills=("research",),
            )
        )


def test_verified_research_teaches_model_routing(tmp_path):
    from aurora.inference.model_performance import ModelPerformanceStore
    from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
    from aurora.research.knowledge import TrustedKnowledgeBuilder
    from aurora.research.learning import ResearchLearningGate
    from aurora.research.models import EvidenceRecord, EvidenceState, ResearchFinding, SourceRecord
    from aurora.research.store import ResearchStore
    from aurora.skills import SkillRegistry
    from aurora.skills.models import SkillSpec

    research_store = ResearchStore(tmp_path / "research.db")
    research_store.record_source(
        SourceRecord(
            source_id="src-trusted",
            url="https://example.test/trusted",
            retrieved_at="2026-09-28T00:00:00+00:00",
            content_sha256="sha-trusted",
            status_code=200,
            content_text="Verified research finding.",
        )
    )
    evidence = EvidenceRecord(
        evidence_id="ev-trusted",
        source_id="src-trusted",
        claim="Verified research finding.",
    ).verify("independently verified")
    research_store.record_evidence(evidence)
    finding = ResearchFinding(
        finding_id="finding-trusted",
        claim="Verified research finding.",
        evidence_ids=("ev-trusted",),
        confidence=1.0,
        state=EvidenceState.VERIFIED,
    )
    artifact, _ = TrustedKnowledgeBuilder(research_store).build(
        finding,
        (evidence,),
    )
    gate = ResearchLearningGate(research_store)

    skills = SkillRegistry({
        "research": SkillSpec(
            "research",
            "1.0.0",
            "Research specialist",
            "Research using verified evidence.",
            "Use verified evidence.",
            frozenset(),
            ("source_check",),
            ("citation_check",),
            ("research",),
            model_preferences=("model/b", "model/a"),
        )
    })
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher",
                "researcher",
                "research",
                "Use evidence.",
                frozenset(),
                (),
                ("research",),
            )
        },
        departments={
            "research": DepartmentSpec(
                "research",
                "Research",
                ("researcher",),
                (),
                frozenset(),
            )
        },
    )

    class LearningOrchestration:
        def __init__(self):
            self.calls = []

        def run_task(self, **kwargs):
            from aurora.inference.models import InferenceRequest, ModelSpec
            from aurora.inference.router import ModelRouter

            class CapturingRouter(ModelRouter):
                def generate(self, request, capability=None, preferred_models=None):
                    decision = self.route(
                        request,
                        capability=capability,
                        preferred_models=preferred_models,
                    )
                    return InferenceResponse(
                        model=decision.selected_model,
                        provider="fake",
                        content=(
                            "result\n"
                            "VERIFY:source_check=pass\n"
                            "QUALITY:citation_check=pass"
                        ),
                    )

            router = CapturingRouter([
                ModelSpec(
                    "model/a",
                    "fake",
                    "https://example.invalid/a",
                    quality_score=0.80,
                    latency_score=0.80,
                    cost_score=0.80,
                    specialties=("research",),
                ),
                ModelSpec(
                    "model/b",
                    "fake",
                    "https://example.invalid/b",
                    quality_score=0.60,
                    latency_score=0.60,
                    cost_score=0.60,
                    specialties=("research",),
                ),
            ])
            request = InferenceRequest(
                messages=[{"role": "user", "content": kwargs["objective"]}],
                metadata={
                    "skill_tags": kwargs.get("skill_tags", ()),
                    "task_type": kwargs.get("task_type", ""),
                    "learned_model_scores": kwargs.get("learned_model_scores", {}),
                },
            )
            response = router.generate(
                request,
                preferred_models=kwargs.get("model_preferences", ()),
            )
            self.calls.append((response.model, kwargs.get("context"), kwargs.get("learned_model_scores", {})))
            return TaskResult(
                task_id=kwargs["task_id"],
                role=kwargs["role"],
                response=response,
            )

    orchestration = LearningOrchestration()
    skill_learning = __import__(
        "aurora.skills.learning", fromlist=["SkillLearningStore"]
    ).SkillLearningStore(tmp_path / "skills.db")
    model_performance = ModelPerformanceStore(tmp_path / "model-performance.db")
    task_engine = TaskEngine(
        orchestration,
        tmp_path / "workspace",
        organization=organization,
        skill_registry=skills,
        skill_learning=skill_learning,
        model_performance=model_performance,
        research_learning_gate=gate,
    )

    artifact_arg = (f"artifact:{artifact.artifact_id}",)
    first = task_engine.execute_one(
        DelegatedTask(
            "learn-research-1",
            "researcher",
            "analyze verified research evidence",
            args=artifact_arg,
            skills=("research",),
        )
    )
    second = task_engine.execute_one(
        DelegatedTask(
            "learn-research-2",
            "researcher",
            "analyze verified research evidence",
            args=artifact_arg,
            skills=("research",),
        )
    )

    scope = {
        "research": "1.0.0",
    }
    assert first.status is TaskStatus.SUCCEEDED
    assert second.status is TaskStatus.SUCCEEDED
    assert first.research_trust_state is EvidenceState.VERIFIED
    assert first.trusted_research_artifact_ids == (artifact.artifact_id,)
    assert orchestration.calls[0][0] == "model/a"
    assert orchestration.calls[1][0] == "model/a"
    assert model_performance.trusted_scores_for(
        skill_ids=("research",),
        skill_versions=scope,
        task_type="research",
        context_bucket="small",
    ) == {"model/a": 1.0}

    third = task_engine.execute_one(
        DelegatedTask(
            "learn-research-3",
            "researcher",
            "analyze verified research evidence",
            args=artifact_arg,
            skills=("research",),
        )
    )
    assert third.status is TaskStatus.SUCCEEDED
    assert orchestration.calls[2][0] == "model/a"
    assert orchestration.calls[0][2] == {}
    assert orchestration.calls[1][2] == {}
    assert orchestration.calls[2][2] == {"model/a": 1.0}
    assert "Verified research knowledge authorized for this task." in (
        orchestration.calls[0][1][0]["content"]
    )


def test_task_engine_propagates_failed_workbench_status_instead_of_marking_success(tmp_path):
    class FailedWorkbenchOrchestration:
        def run_capability_task(self, **kwargs):
            return SimpleNamespace(
                execution=SimpleNamespace(
                    status="failed",
                    error="workbench preflight refused the operation",
                    output={"ready": False},
                    receipt_id="receipt-failed",
                )
            )

        def run_task(self, **kwargs):
            raise AssertionError("Workbench capability must use the governed capability path")

    task = DelegatedTask(
        "preflight",
        "operator",
        "Verify governed Workbench readiness",
        capability="workbench:preflight",
    )
    result = TaskEngine(FailedWorkbenchOrchestration(), tmp_path).execute_one(task)

    assert result.status is TaskStatus.FAILED
    assert result.error == "workbench preflight refused the operation"
    assert result.capability_result.execution.output == {"ready": False}



def test_durable_workbench_task_uses_same_idempotency_key_across_worker_attempts(tmp_path):
    class CaptureWorkbenchOrchestration:
        def __init__(self):
            self.requests = []

        def run_capability_task(self, **kwargs):
            self.requests.append(kwargs["request"])
            return SimpleNamespace(
                execution=SimpleNamespace(
                    status="succeeded",
                    output={"completed": True},
                )
            )

        def run_task(self, **kwargs):
            raise AssertionError("Workbench capability must use the governed capability path")

    orchestration = CaptureWorkbenchOrchestration()
    task_engine = TaskEngine(orchestration, tmp_path / "workspace")
    task = DelegatedTask(
        "write-1",
        "engineer",
        "write one deterministic output",
        capability="workbench:file:write",
        args=("out.txt", "value"),
    )

    first = task_engine.execute_one(
        task,
        execution_scope="mission-1:write-1:attempt:1",
    )
    second = task_engine.execute_one(
        task,
        execution_scope="mission-1:write-1:attempt:2",
    )

    assert first.status is TaskStatus.SUCCEEDED
    assert second.status is TaskStatus.SUCCEEDED
    keys = [item.constraints["idempotency_key"] for item in orchestration.requests]
    assert keys == [
        "task:mission-1:write-1:workbench:file:write",
        "task:mission-1:write-1:workbench:file:write",
    ]
