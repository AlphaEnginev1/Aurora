import json

import pytest

from aurora.context import AuroraContextPlane, ContextGate
from aurora.inference import models as model_contracts
from aurora.inference.models import InferenceRequest, InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter, ModelRoutingError
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director_bootstrap import (
    build_context_knowledge_adapter,
    build_context_mailbox_adapter,
    build_context_runtime_adapter,
)
from aurora.orchestration.task_store import DurableTaskStore
from aurora.projects import ProjectRegistry
from aurora.skills import SkillRegistry
from aurora.research.store import ResearchStore


def _router_fixture(tmp_path):
    organization = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                "researcher",
                "researcher",
                "research",
                "Research",
                frozenset({"reasoning"}),
                ("model/research",),
                (),
            )
        },
        departments={
            "research": DepartmentSpec(
                "research",
                "Research",
                ("researcher",),
                ("model/research",),
                frozenset({"reasoning"}),
            )
        },
    )
    task_store = DurableTaskStore(tmp_path / "tasks.db")
    research_store = ResearchStore(tmp_path / "research.db")
    plane = AuroraContextPlane(
        project_registry=ProjectRegistry({}),
        organization=organization,
        skill_registry=SkillRegistry({}),
        models=(
            ModelSpec(
                "model/research",
                "nvidia_api",
                "https://example.invalid",
                capabilities=("reasoning",),
                context_window=8192,
            ),
        ),
        runtime_context=build_context_runtime_adapter(task_store),
        mailbox_context=build_context_mailbox_adapter(task_store),
        knowledge_context=build_context_knowledge_adapter(research_store),
    )
    return ModelRouter(
        models=list(plane.models),
        context_gate=ContextGate(plane),
        enforce_context=True,
    )


def test_router_requires_context_gate_when_enforcement_is_enabled():
    router = ModelRouter(
        models=[
            ModelSpec(
                "model/research",
                "nvidia_api",
                "https://example.invalid",
                capabilities=("reasoning",),
            )
        ],
        enforce_context=True,
    )
    request = InferenceRequest(
        messages=[{"role": "user", "content": "test"}],
        metadata={"role": "researcher", "task_id": "task-1"},
    )

    with pytest.raises(ModelRoutingError, match="ContextGate is required"):
        router.generate(request, capability="reasoning")


def test_router_injects_verified_manifest_before_provider(monkeypatch, tmp_path):
    router = _router_fixture(tmp_path)
    seen = {}

    class FakeProvider:
        def __init__(self, spec, *, api_key):
            self.spec = spec
            self.api_key = api_key

        def generate(self, request, *, selected_model, execution_id):
            seen["request"] = request
            return InferenceResponse(
                model=selected_model,
                provider="nvidia_api",
                content="ok",
                request_id=request.request_id,
                execution_id=execution_id,
                selected_model=selected_model,
                provider_model=selected_model,
                reported_model=selected_model,
            )

    monkeypatch.setattr(
        "aurora.inference.router.NVIDIAAPIProvider",
        FakeProvider,
    )

    response = router.generate(
        InferenceRequest(
            messages=[{"role": "user", "content": "do research"}],
            metadata={"role": "researcher", "task_id": "task-1"},
        ),
        capability="reasoning",
    )

    request = seen["request"]
    assert request.metadata["aurora_context_verified"] is True
    assert request.metadata["aurora_context_manifest_id"].startswith("ctx-")
    assert request.metadata["aurora_context_scope"] == "global"
    assert request.messages[0]["role"] == "system"
    assert "source=aurora.identity" in request.messages[0]["content"]
    assert request.messages[-1]["content"] == "do research"
    assert response.execution_id


def test_router_fails_closed_when_runtime_state_is_unknown(monkeypatch, tmp_path):
    organization = OrganizationRegistry(
        agents={},
        departments={},
    )
    task_store = DurableTaskStore(tmp_path / "tasks.db")
    research_store = ResearchStore(tmp_path / "research.db")
    plane = AuroraContextPlane(
        project_registry=ProjectRegistry({}),
        organization=organization,
        skill_registry=SkillRegistry({}),
        models=(
            ModelSpec(
                "model/research",
                "nvidia_api",
                "https://example.invalid",
                capabilities=("reasoning",),
            ),
        ),
        mailbox_context=build_context_mailbox_adapter(task_store),
        knowledge_context=build_context_knowledge_adapter(research_store),
    )
    router = ModelRouter(
        models=list(plane.models),
        context_gate=ContextGate(plane),
        enforce_context=True,
    )
    request = InferenceRequest(
        messages=[{"role": "user", "content": "test"}],
        metadata={"role": "researcher", "task_id": "task-1"},
    )

    with pytest.raises(ModelRoutingError, match="runtime_state is unknown"):
        router.generate(request, capability="reasoning")


def test_router_uses_project_scope_when_project_identity_is_present(monkeypatch, tmp_path):
    router = _router_fixture(tmp_path)
    seen = {}

    class FakeProvider:
        def __init__(self, spec, *, api_key):
            pass

        def generate(self, request, *, selected_model, execution_id):
            seen["request"] = request
            return InferenceResponse(
                model=selected_model,
                provider="nvidia_api",
                content=json.dumps({"ok": True}),
                request_id=request.request_id,
                execution_id=execution_id,
                selected_model=selected_model,
                provider_model=selected_model,
                reported_model=selected_model,
            )

    monkeypatch.setattr("aurora.inference.router.NVIDIAAPIProvider", FakeProvider)

    request = InferenceRequest(
        messages=[{"role": "user", "content": "project task"}],
        metadata={
            "role": "researcher",
            "task_id": "task-1",
            "project_id": "missing-project",
        },
    )
    with pytest.raises(ModelRoutingError, match="unknown Aurora project"):
        router.generate(request, capability="reasoning")
