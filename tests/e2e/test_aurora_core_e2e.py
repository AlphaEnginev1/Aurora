"""Canonical Aurora Core end-to-end integration proof.

This test composes the already-verified runtime boundaries into one deterministic
cycle. It does not replace the dedicated live provider/research/network gates.
"""

from __future__ import annotations

from pathlib import Path

from aurora.inference.model_performance import ModelPerformanceStore
from aurora.inference.models import InferenceResponse
from aurora.organization.registry import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director import DirectorPlanner
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.director_runtime import DirectorRuntime
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.engine import OrchestrationEngine
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_engine import TaskEngine, TaskStatus
from aurora.orchestration.task_store import DurableTaskStore
from aurora.orchestration.task_worker import WorkerPolicy
from aurora.research.knowledge import TrustedKnowledgeBuilder
from aurora.research.learning import ResearchLearningGate
from aurora.research.models import EvidenceRecord, EvidenceState, SourceRecord
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore
from aurora.skills.learning import SkillLearningStore
from aurora.skills.models import SkillSpec
from aurora.skills.registry import SkillRegistry


class CoreRouter:
    def __init__(self):
        self.requests = []

    def generate(self, request, *, capability="reasoning", preferred_models=None):
        self.requests.append(request)
        return InferenceResponse(
            model="core/model",
            provider="controlled",
            content=(
                "verified execution result\n"
                "VERIFY:source_check=pass\n"
                "QUALITY:citation_check=pass"
            ),
            request_id=request.request_id,
            execution_id=f"execution-{len(self.requests)}",
        )


class CoreDirectorProvider:
    provider_id = "controlled"

    def __init__(self):
        self.calls = 0
        self.review_contexts = []

    def generate(self, messages, model, role, tools=()):
        self.calls += 1
        if role == "director_planner":
            return DirectorModelResponse(
                content=(
                    '{"tasks":['
                    '{"task_id":"research_evidence","role":"researcher",'
                    '"objective":"investigate Aurora evidence",'
                    '"capability":"research",'
                    '"args":["https://example.test/aurora"],'
                    '"depends_on":[],"skills":[]},'
                    '{"task_id":"analyze_evidence","role":"researcher",'
                    '"objective":"analyze verified Aurora evidence",'
                    '"capability":null,'
                    '"args":["artifact:kart-core"],'
                    '"depends_on":["research_evidence"],'
                    '"skills":["research"]}'
                    ']}'
                ),
                model="controlled/director",
                provider="controlled",
                request_id=f"planner-{self.calls}",
                execution_id=f"planner-execution-{self.calls}",
                tool_calls=(),
            )
        if role == "director_reviewer":
            self.review_contexts.append(tuple(messages))
            return DirectorModelResponse(
                content=(
                    '{"decision":"complete",'
                    '"rationale":"Execution, evidence, evaluation and learned routing '
                    'are all present and the mission objective is satisfied.",'
                    '"tasks":[]}'
                ),
                model="controlled/director",
                provider="controlled",
                request_id=f"review-{self.calls}",
                execution_id=f"review-execution-{self.calls}",
                tool_calls=(),
            )
        raise AssertionError(f"unexpected Director role: {role}")


class CoreGateway:
    def get(self, url):
        if "search" in url:
            return (
                b'{"results":[{"url":"https://example.test/aurora",'
                b'"title":"Aurora evidence","snippet":"verified architecture evidence"}]}'
            )
        return b"Aurora evidence source." 


def _seed_trusted_artifact(store: ResearchStore) -> None:
    source = SourceRecord(
        source_id="src-core",
        url="https://example.test/aurora",
        retrieved_at="2026-09-30T00:00:00+00:00",
        content_sha256="sha-core",
        status_code=200,
        content_text="Aurora evidence source.",
    )
    store.record_source(source)
    evidence = EvidenceRecord(
        evidence_id="ev-core",
        source_id=source.source_id,
        claim="Aurora evidence source.",
    ).verify("independent verification")
    store.record_evidence(evidence)
    finding = TrustedKnowledgeBuilder(store).build(
        __import__("aurora.research.models", fromlist=["ResearchFinding"]).ResearchFinding(
            finding_id="finding-core",
            claim="Aurora evidence source.",
            evidence_ids=(evidence.evidence_id,),
            confidence=1.0,
            state=EvidenceState.VERIFIED,
        ),
        (evidence,),
    )[0]
    store.record_knowledge_artifact(finding)


def test_aurora_core_full_cycle(tmp_path: Path):
    router = CoreRouter()
    research_store = ResearchStore(tmp_path / "research.db")
    _seed_trusted_artifact(research_store)
    research_service = ResearchService(
        CoreGateway(),
        research_store,
    )

    skills = SkillRegistry(
        {
            "research": SkillSpec(
                "research",
                "1.0.0",
                "Research specialist",
                "Research using verified evidence.",
                "Use verified evidence and report the evidence markers.",
                frozenset(),
                ("source_check",),
                ("citation_check",),
                ("research",),
                model_preferences=("core/model",),
            )
        }
    )
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher",
                "researcher",
                "research",
                "Investigate and validate evidence.",
                frozenset(),
                ("core/model",),
                ("research",),
            )
        },
        departments={
            "research": DepartmentSpec(
                "research",
                "Research",
                ("researcher",),
                ("core/model",),
                frozenset(),
            )
        },
    )
    skills.validate_organization(organization)

    performance = ModelPerformanceStore(tmp_path / "model-performance.db")
    task_engine = TaskEngine(
        OrchestrationEngine(router),
        tmp_path / "workspace",
        organization=organization,
        skill_registry=skills,
        skill_learning=SkillLearningStore(tmp_path / "skills.db"),
        model_performance=performance,
        research_service=research_service,
        research_learning_gate=ResearchLearningGate(research_store),
    )
    planner = DirectorPlanner(
        provider=CoreDirectorProvider(),
        router=None,
        organization=organization,
        skill_registry=skills,
    )
    provider = planner.provider
    durable = DurableDirectorService(
        planner=planner,
        task_engine=task_engine,
        store=DurableTaskStore(tmp_path / "tasks.db"),
        event_bus=DurableEventBus(DurableTaskStore(tmp_path / "events.db")),
    )
    # Reuse the durable store/event bus from the same database for a real runtime.
    store = DurableTaskStore(tmp_path / "runtime.db")
    event_bus = DurableEventBus(store)
    durable = DurableDirectorService(
        planner=planner,
        task_engine=task_engine,
        store=store,
        event_bus=event_bus,
    )
    runtime = DirectorRuntime(
        director=durable,
        store=store,
        event_bus=event_bus,
        worker_policy=WorkerPolicy(lease_seconds=30, max_attempts=2, idle_sleep_seconds=0),
        worker_id="aurora-core-e2e",
        event_consumer_id="aurora-core-e2e-events",
        worker_concurrency=1,
        max_review_rounds=1,
    )

    submission = runtime.submit_mission(
        objective="Investigate Aurora evidence, execute the analysis, evaluate the result, "
        "and complete the mission using learned routing evidence."
    )
    result = runtime.run_until_terminal(submission, max_cycles=20)

    assert result.status is TaskStatus.SUCCEEDED
    assert [item.task.task_id for item in result.tasks] == [
        "research_evidence",
        "analyze_evidence",
    ]
    assert all(item.status is TaskStatus.SUCCEEDED for item in result.tasks)

    learned = performance.trusted_scores_for(
        skill_ids=("research",),
        skill_versions={"research": "1.0.0"},
        task_type="research",
        context_bucket="small",
        minimum_evaluations=1,
        minimum_evidence_completeness=1.0,
    )
    assert learned == {"core/model": 1.0}

    events = [event.event_type for event in store.events(submission.mission_id)]
    required = {
        "mission_submitted",
        "worker.task_succeeded",
        "director_review_started",
        "director_review",
        "mission_succeeded",
    }
    assert required.issubset(events)
    assert any(
        "Observed specialist evaluations:" in message.get("content", "")
        and "score=1.0" in message.get("content", "")
        for review in provider.review_contexts
        for message in review
    )
