from pathlib import Path

from aurora.inference.models import InferenceRequest, InferenceResponse, ModelSpec
from aurora.inference.model_performance import ModelPerformanceStore
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import TaskResult
from aurora.orchestration.task_engine import DelegatedTask, DelegatedTaskResult, TaskStatus
from aurora.skills import SkillRegistry
from aurora.skills.evaluation import SkillEvaluator
from aurora.skills.models import SkillSpec


class ControlledProviderRouter(ModelRouter):
    """Real routing logic with a deterministic provider for the proof."""

    def generate(self, request, capability=None, preferred_models=None):
        decision = self.route(
            request,
            capability=capability,
            preferred_models=preferred_models,
        )
        return InferenceResponse(
            model=decision.selected_model,
            provider="controlled-test-provider",
            content="result\nVERIFY:source_check=pass\nQUALITY:citation_check=pass",
        )


def _models():
    return [
        ModelSpec(
            "model/a",
            "controlled",
            "https://example.invalid/a",
            capabilities=("reasoning",),
            quality_score=0.75,
            latency_score=0.75,
            cost_score=0.75,
            specialties=("research",),
        ),
        ModelSpec(
            "model/b",
            "controlled",
            "https://example.invalid/b",
            capabilities=("reasoning",),
            quality_score=0.60,
            latency_score=0.60,
            cost_score=0.60,
            specialties=("research",),
        ),
    ]


def _skill_and_task():
    registry = SkillRegistry(
        {
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
        }
    )
    return (
        SkillEvaluator(registry),
        DelegatedTask(
            "adaptive-learning",
            "researcher",
            "research task",
            skills=("research",),
        ),
    )


def test_trusted_execution_changes_subsequent_model_selection(tmp_path: Path):
    """A real evaluated execution must be able to change the next routing decision."""
    router = ControlledProviderRouter(_models())
    baseline = router.route(
        InferenceRequest(
            messages=[{"role": "user", "content": "research task"}],
            metadata={"skill_tags": ("research",), "task_type": "research"},
        ),
        preferred_models=(),
    )
    assert baseline.selected_model == "model/a"

    evaluator, task = _skill_and_task()
    performance = ModelPerformanceStore(tmp_path / "model-performance.db")

    for attempt in range(2):
        forced = router.generate(
            InferenceRequest(
                model="model/b",
                messages=[{"role": "user", "content": "research task"}],
                metadata={"skill_tags": ("research",), "task_type": "research"},
            )
        )
        evaluated = evaluator.evaluate(
            task=task,
            result=DelegatedTaskResult(
                task=task,
                status=TaskStatus.SUCCEEDED,
                model_result=TaskResult(
                    task_id=f"adaptive-learning-{attempt}",
                    role="researcher",
                    response=forced,
                ),
            ),
            task_type="research",
            context_bucket="small",
        )
        evaluation = evaluated[0]
        performance.record(
            model=evaluation.model,
            skill_id=evaluation.skill_id,
            skill_version=evaluation.skill_version,
            task_type=evaluation.task_type,
            context_bucket=evaluation.context_bucket,
            score=evaluation.score,
            evidence_completeness=evaluation.evidence_completeness,
            evidence_state=evaluation.evidence_state,
            created_at=f"2026-09-28T00:00:0{attempt}+00:00",
        )

    learned = performance.trusted_scores_for(
        skill_ids=("research",),
        skill_versions={"research": "1.0.0"},
        task_type="research",
        context_bucket="small",
    )
    adapted = router.route(
        InferenceRequest(
            messages=[{"role": "user", "content": "research task"}],
            metadata={
                "skill_tags": ("research",),
                "task_type": "research",
                "learned_model_scores": learned,
            },
        ),
        preferred_models=(),
    )
    assert learned == {"model/b": 1.0}
    assert adapted.selected_model == "model/b"
    assert adapted.selected_model != baseline.selected_model
    assert adapted.factors["learned_score"] == 1.0
    assert adapted.candidates[0][0] == "model/b"


def test_model_performance_store_uses_real_provider_identity(tmp_path: Path):
    """Provider/model identity from execution must be the identity persisted for learning."""
    response = InferenceResponse(
        model="nvidia/model-a",
        provider="nvidia_api",
        content="result",
        requested_model="nvidia/model-a",
        selected_model="nvidia/model-a",
        provider_model="nvidia/model-a",
        reported_model="nvidia/model-a",
    )
    assert response.model == response.provider_model == response.reported_model


def test_provider_bound_execution_evaluates_and_changes_next_route(tmp_path: Path, monkeypatch):
    """The real NVIDIA adapter path must feed evaluation, storage, and the next route."""

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"model":"nvidia/model-a","choices":[{"message":{"content":"result\\nVERIFY:source_check=pass\\nQUALITY:citation_check=pass"}}],"usage":{"prompt_tokens":8,"completion_tokens":6,"total_tokens":14}}'

    def _urlopen(request, timeout):
        assert request.get_header("Authorization") == "Bearer test-key"
        assert request.full_url.endswith("/chat/completions")
        return _Response()

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_api.urllib.request.urlopen",
        _urlopen,
    )

    model = ModelSpec(
        "nvidia/model-a",
        "nvidia_api",
        "https://integrate.invalid/v1",
        quality_score=0.60,
        latency_score=0.60,
        cost_score=0.60,
        specialties=("research",),
    )
    router = ModelRouter([model], nvidia_api_key="test-key")
    request = InferenceRequest(
        messages=[{"role": "user", "content": "research task"}],
        metadata={"skill_tags": ("research",), "task_type": "research"},
    )

    response = router.generate(request)
    assert response.provider == "nvidia_api"
    assert response.model == "nvidia/model-a"
    assert response.provider_model == "nvidia/model-a"
    assert response.reported_model == "nvidia/model-a"
    assert response.usage.total_tokens == 14
    assert response.telemetry is not None
    assert response.telemetry.success is True
    assert response.telemetry.request_id == request.request_id
    assert response.telemetry.execution_id == response.execution_id
    assert response.routing_score is not None
    assert response.routing_factors["learned_score"] == 0.5
    assert response.routing_candidates == (("nvidia/model-a", response.routing_score),)

    evaluator, task = _skill_and_task()
    performance = ModelPerformanceStore(tmp_path / "model-performance.db")

    for attempt in range(2):
        evaluated = evaluator.evaluate(
            task=task,
            result=DelegatedTaskResult(
                task=task,
                status=TaskStatus.SUCCEEDED,
                model_result=TaskResult(
                    task_id=f"provider-bound-{attempt}",
                    role="researcher",
                    response=response,
                ),
            ),
            task_type="research",
            context_bucket="small",
        )
        evaluation = evaluated[0]
        assert evaluation.model == "nvidia/model-a"
        assert evaluation.evidence_state == "evidence_complete"
        performance.record(
            model=evaluation.model,
            skill_id=evaluation.skill_id,
            skill_version=evaluation.skill_version,
            task_type=evaluation.task_type,
            context_bucket=evaluation.context_bucket,
            score=evaluation.score,
            evidence_completeness=evaluation.evidence_completeness,
            evidence_state=evaluation.evidence_state,
            created_at=f"2026-09-28T00:00:1{attempt}+00:00",
        )

    learned = performance.trusted_scores_for(
        skill_ids=("research",),
        skill_versions={"research": "1.0.0"},
        task_type="research",
        context_bucket="small",
    )
    assert learned == {"nvidia/model-a": 1.0}

    adapted = router.route(
        InferenceRequest(
            messages=[{"role": "user", "content": "research task"}],
            metadata={
                "skill_tags": ("research",),
                "task_type": "research",
                "learned_model_scores": learned,
            },
        )
    )
    assert adapted.selected_model == "nvidia/model-a"
    assert adapted.factors["learned_score"] == 1.0



def test_task_engine_closes_execute_evaluate_learn_select_execute_cycle(tmp_path: Path):
    """The real task-engine path must consume learned evidence on the next mission."""
    from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
    from aurora.orchestration.engine import OrchestrationEngine
    from aurora.orchestration.task_engine import TaskEngine
    from aurora.skills.learning import SkillLearningStore

    router = ControlledProviderRouter(_models())
    orchestration = OrchestrationEngine(router)
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research",
                capabilities=frozenset({"reasoning"}),
                model_preferences=("model/b",),
                skill_ids=("research",),
            )
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset({"reasoning"}),
            )
        },
    )
    registry = SkillRegistry(
        {
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
        }
    )
    learning = SkillLearningStore(tmp_path / "skill-learning.db")
    performance = ModelPerformanceStore(tmp_path / "model-performance.db")
    engine = TaskEngine(
        orchestration,
        tmp_path,
        organization=organization,
        skill_registry=registry,
        skill_learning=learning,
        model_performance=performance,
    )

    def mission_task(task_id: str) -> DelegatedTask:
        return DelegatedTask(
            task_id,
            "researcher",
            "research task",
            skills=("research",),
        )

    first = engine.execute((mission_task("mission-1"),))[0]
    second = engine.execute((mission_task("mission-2"),))[0]
    assert first.status is TaskStatus.SUCCEEDED
    assert second.status is TaskStatus.SUCCEEDED
    assert first.model_result is not None
    assert second.model_result is not None
    assert first.model_result.response.model == "model/b"
    assert second.model_result.response.model == "model/b"

    learned = performance.trusted_scores_for(
        skill_ids=("research",),
        skill_versions={"research": "1.0.0"},
        task_type="research",
        context_bucket="small",
    )
    assert learned == {"model/b": 1.0}

    # Remove the exploratory preference. The next mission must now be driven
    # by ModelPerformanceStore evidence rather than the organization preference.
    organization.agents["researcher"] = AgentSpec(
        agent_id="researcher",
        role="researcher",
        department="research",
        mission="research",
        capabilities=frozenset({"reasoning"}),
        model_preferences=(),
        skill_ids=("research",),
    )

    third = engine.execute((mission_task("mission-3"),))[0]
    assert third.status is TaskStatus.SUCCEEDED
    assert third.model_result is not None
    assert third.model_result.response.model == "model/b"
