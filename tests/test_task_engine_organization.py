from pathlib import Path

import pytest

from aurora.inference.models import InferenceResponse
from aurora.inference.router import ModelRouter
from aurora.orchestration.engine import OrchestrationEngine, TaskResult
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine, TaskEngineError
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry


class CaptureRouter(ModelRouter):
    def __init__(self):
        super().__init__([
            type("Model", (), {
                "id": "model/a",
                "provider": "fake",
                "endpoint": "https://example.invalid",
                "capabilities": ("reasoning",),
                "context_window": None,
                "enabled": True,
            })()
        ])
        self.received_preferences = None

    def generate(self, request, capability=None, preferred_models=None):
        self.received_preferences = tuple(preferred_models or ())
        return InferenceResponse(
            model="model/a",
            provider="fake",
            content="ok",
        )


def make_org() -> OrganizationRegistry:
    return OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research",
                capabilities=frozenset({"tool:curl"}),
                model_preferences=("model/a",),
            )
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=("model/a",),
                allowed_capabilities=frozenset({"tool:curl"}),
            )
        },
    )


def test_task_engine_rejects_role_not_in_organization(tmp_path: Path):
    router = CaptureRouter()
    engine = TaskEngine(
        OrchestrationEngine(router),
        tmp_path / "workspace",
        organization=make_org(),
    )
    with pytest.raises(TaskEngineError, match="unknown Aurora agent role"):
        engine.validate([DelegatedTask("t", "unknown", "work")])


def test_task_engine_rejects_unauthorized_capability(tmp_path: Path):
    router = CaptureRouter()
    engine = TaskEngine(
        OrchestrationEngine(router),
        tmp_path / "workspace",
        organization=make_org(),
    )
    with pytest.raises(TaskEngineError, match="not authorized"):
        engine.validate([
            DelegatedTask("t", "researcher", "download", capability="tool:git")
        ])


def test_task_engine_passes_role_model_preferences_to_orchestration(tmp_path: Path):
    router = CaptureRouter()
    engine = TaskEngine(
        OrchestrationEngine(router),
        tmp_path / "workspace",
        organization=make_org(),
    )
    result = engine.execute([DelegatedTask("t", "researcher", "reason")])
    assert result[0].status.value == "succeeded"
    assert router.received_preferences == ("model/a",)
