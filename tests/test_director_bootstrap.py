from dataclasses import asdict
import json

from aurora.context import AuroraContextPlane, ContextGate
from aurora.inference.models import ModelSpec
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.director_bootstrap import (
    DirectorBootstrap,
    build_context_knowledge_adapter,
    build_context_mailbox_adapter,
    build_context_runtime_adapter,
)
from aurora.orchestration.director_decision_knowledge_store import DirectorDecisionKnowledgeStore
from aurora.orchestration.director_decision_performance import DirectorDecisionPerformanceStore
from aurora.orchestration.director_decision_policy_active_store import DirectorDecisionPolicyActiveStore
from aurora.orchestration.director_decision_policy_contract_store import DirectorDecisionPolicyContractStore
from aurora.orchestration.director_decision_policy_proposal_store import DirectorDecisionPolicyProposalStore
from aurora.orchestration.task_store import DurableTaskStore
from aurora.projects import ProjectRegistry
from aurora.skills import SkillRegistry


def _registries():
    organization = OrganizationRegistry(
        agents={
            "director-support": AgentSpec(
                "director-support", "director-support", "operations", "Support",
                frozenset({"tool:rg"}), ("model/test",), ()
            )
        },
        departments={
            "operations": DepartmentSpec(
                "operations", "Operate", ("director-support",), ("model/test",),
                frozenset({"tool:rg"})
            )
        },
    )
    return (
        ProjectRegistry({}),
        organization,
        SkillRegistry({}),
        (ModelSpec("model/test", "test-provider", "https://example.invalid"),),
    )


def test_director_bootstrap_loads_authoritative_startup_state(tmp_path):
    projects, organization, skills, models = _registries()
    task_store = DurableTaskStore(tmp_path / "tasks.db")
    research_store_path = tmp_path / "research.db"
    from aurora.research.store import ResearchStore
    research_store = ResearchStore(research_store_path)

    gate = ContextGate(
        AuroraContextPlane(
            projects,
            organization,
            skills,
            models,
            runtime_context=build_context_runtime_adapter(task_store),
            mailbox_context=build_context_mailbox_adapter(task_store),
            knowledge_context=build_context_knowledge_adapter(research_store),
        )
    )
    policy_path = tmp_path / "inference-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_capability": "reasoning",
                "providers": {
                    "test": {
                        "enabled": True,
                        "api_key_env": "TEST_KEY",
                        "base_url": "https://example.invalid/v1",
                    }
                },
                "models": [
                    {
                        "id": "model/test",
                        "provider": "test",
                        "enabled": True,
                        "capabilities": ["reasoning"],
                        "context_window": 8192,
                    }
                ],
                "routing": {"allow_fallback": True},
            }
        ),
        encoding="utf-8",
    )
    bootstrap = DirectorBootstrap(
        context_gate=gate,
        model_policy_path=policy_path,
        decision_performance_store=DirectorDecisionPerformanceStore(tmp_path / "tasks.db"),
        active_policy_store=DirectorDecisionPolicyActiveStore(tmp_path / "tasks.db"),
        policy_proposal_store=DirectorDecisionPolicyProposalStore(tmp_path / "tasks.db"),
        policy_contract_store=DirectorDecisionPolicyContractStore(tmp_path / "tasks.db"),
        decision_knowledge_store=DirectorDecisionKnowledgeStore(tmp_path / "tasks.db"),
    )

    state = bootstrap.initialize()

    assert state.context.scope.value == "director"
    assert state.context.project_id is None
    assert state.director_model_policy["default_capability"] == "reasoning"
    assert state.active_decision_policy is None
    assert state.active_decision_contract is None
    assert state.decision_performance == ()
    assert state.decision_knowledge == ()
    assert any("source=director.model_policy" in item["content"] for item in state.messages)
    assert state.boot_id.startswith("boot-")


def test_bootstrap_adapters_return_known_empty_indexes(tmp_path):
    task_store = DurableTaskStore(tmp_path / "tasks.db")
    from aurora.research.store import ResearchStore
    research_store = ResearchStore(tmp_path / "research.db")

    assert build_context_runtime_adapter(task_store)(None)["mission_count"] == 0
    assert build_context_mailbox_adapter(task_store)(None)
    assert build_context_knowledge_adapter(research_store)(None)


def test_mailbox_adapter_scopes_project_context_without_cross_project_events(tmp_path):
    task_store = DurableTaskStore(tmp_path / "tasks.db")
    event_bus = DurableEventBus(task_store)
    project_a = event_bus.publish(
        mission_id="mission-a",
        event_type="project_a_result",
        payload={"detail": "A-only detail"},
        project_id="project-a",
    )
    project_b = event_bus.publish(
        mission_id="mission-b",
        event_type="project_b_result",
        payload={"detail": "B-only detail"},
        project_id="project-b",
    )
    unscoped = event_bus.publish(
        mission_id="mission-global",
        event_type="unscoped_task_result",
        payload={"detail": "project attribution unavailable"},
    )

    mailbox = build_context_mailbox_adapter(task_store)
    project_a_messages = mailbox("project-a")
    global_messages = mailbox(None)

    # Unscoped task events are ambiguous, not proven global; they must not be
    # leaked into a project-scoped model context.
    assert [item["event_id"] for item in project_a_messages] == [project_a.event_id]
    assert project_a_messages[0]["project_id"] == "project-a"
    assert "A-only detail" in str(project_a_messages[0]["payload"])
    assert "B-only detail" not in str(project_a_messages)
    assert "project attribution unavailable" not in str(project_a_messages)

    # Director/global scope can still consult the cross-project event history.
    assert [item["event_id"] for item in global_messages] == [
        project_a.event_id,
        project_b.event_id,
        unscoped.event_id,
    ]


def test_bootstrap_policy_never_exposes_secret_values(tmp_path):
    projects, organization, skills, models = _registries()
    task_store = DurableTaskStore(tmp_path / "tasks.db")
    from aurora.research.store import ResearchStore
    research_store = ResearchStore(tmp_path / "research.db")
    gate = ContextGate(AuroraContextPlane(
        projects, organization, skills, models,
        runtime_context=build_context_runtime_adapter(task_store),
        mailbox_context=build_context_mailbox_adapter(task_store),
        knowledge_context=build_context_knowledge_adapter(research_store),
    ))
    policy_path = tmp_path / "inference-policy.json"
    policy_path.write_text(json.dumps({
        "version": 1,
        "default_capability": "reasoning",
        "providers": {"test": {"enabled": True, "api_key_env": "TEST_KEY", "base_url": "https://example.invalid"}},
        "models": [{"id": "model/test", "provider": "test", "endpoint": "https://example.invalid", "enabled": True}],
        "routing": {}
    }), encoding="utf-8")
    state = DirectorBootstrap(
        gate, policy_path,
        DirectorDecisionPerformanceStore(tmp_path / "tasks.db"),
        DirectorDecisionPolicyActiveStore(tmp_path / "tasks.db"),
        DirectorDecisionPolicyProposalStore(tmp_path / "tasks.db"),
        DirectorDecisionPolicyContractStore(tmp_path / "tasks.db"),
        DirectorDecisionKnowledgeStore(tmp_path / "tasks.db"),
    ).initialize()
    rendered = json.dumps(state.director_model_policy)
    assert "NVIDIA_API_KEY" not in rendered
    assert "TEST_KEY" in rendered
