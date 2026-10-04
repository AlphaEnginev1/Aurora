from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from aurora.context import ContextManifest, ContextScope
from aurora.orchestration.director import DirectorPlanner
from aurora.orchestration.director_conversation import DirectorConversationService
from aurora.orchestration.director_provider import DirectorModelResponse
from aurora.orchestration.director_provider_failover import (
    DirectorProviderCandidate,
    DirectorProviderFailoverController,
    DirectorProviderFailoverStore,
)
from aurora.orchestration.durable_director import DurableDirectorService
from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.task_store import DurableTaskStore


class ForcedGeminiFailure(RuntimeError):
    failoverable = True


class FakeProvider:
    def __init__(self, provider_id: str, outcomes: list[object]) -> None:
        self.provider_id = provider_id
        self._outcomes = list(outcomes)
        self.calls = 0

    def generate(self, *, messages, model, role, tools=()):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return DirectorModelResponse(
            content=str(outcome),
            model=model or f"{self.provider_id}-model",
            provider=self.provider_id,
            request_id=f"{self.provider_id}-request-{self.calls}",
            execution_id=f"{self.provider_id}-execution-{self.calls}",
        )


@dataclass(frozen=True)
class BootstrapState:
    boot_id: str
    context: ContextManifest
    director_model_policy: dict

    @property
    def messages(self):
        return self.context.messages


class BootstrapStub:
    def __init__(self) -> None:
        self.context_gate = object()
        self._state = BootstrapState(
            boot_id="boot-test",
            context=ContextManifest(
                manifest_id="ctx-provider-failover",
                created_at="2026-10-04T00:00:00+00:00",
                scope=ContextScope.DIRECTOR,
                project_id=None,
                fragments=(),
                unknown_fields=(),
            ),
            director_model_policy={
                "default_capability": "reasoning",
                "providers": {},
                "models": [
                    {"id": "test/director-model", "provider": "test", "enabled": True}
                ],
                "routing": {},
            },
        )

    def initialize(self):
        return self._state


class RouterStub:
    def configure_context_gate(self, context_gate, *, enforce=True) -> None:
        self.context_gate = context_gate
        self.enforce = enforce


class StubTaskEngine:
    def validate(self, tasks):
        return tasks


def _candidate(provider_id: str, provider: FakeProvider, model: str | None):
    return DirectorProviderCandidate(
        provider_id=provider_id,
        model=model,
        endpoint=None,
        factory=lambda: provider,
    )


def test_same_conversation_session_survives_gemini_failover(tmp_path: Path):
    gemini = FakeProvider("gemini", ["Gemini online", ForcedGeminiFailure("forced")])
    nvidia = FakeProvider("nvidia_api", ["NVIDIA replacement"])
    store = DirectorProviderFailoverStore(str(tmp_path / "conversation.db"))
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=(_candidate("nvidia_api", nvidia, "nvidia/test-model"),),
        store=store,
        activate=lambda _candidate, _provider: None,
    )

    service = DirectorConversationService(
        router=RouterStub(),
        bootstrap=BootstrapStub(),
        db_path=tmp_path / "conversation.db",
        provider=controller,
    )
    session = service.create_session()
    first = service.send_message(session_id=session.session_id, user_input="hello")
    second = service.send_message(session_id=session.session_id, user_input="continue")

    assert first.provider == "gemini"
    assert second.provider == "nvidia_api"
    assert second.turn.session_id == session.session_id
    assert [turn.sequence for turn in service.list_turns(session.session_id)] == [1, 2, 3, 4]
    assert service.get_session(session.session_id).state == "active"


def test_same_mission_and_task_graph_survive_provider_failover(tmp_path: Path):
    gemini = FakeProvider(
        "gemini",
        [
            '{"tasks":[{"task_id":"task-1","role":"researcher","objective":"complete one durable step","capability":null,"args":[],"depends_on":[],"skills":[]}]}',
            ForcedGeminiFailure("forced"),
        ],
    )
    nvidia = FakeProvider(
        "nvidia_api",
        ['{"decision":"complete","rationale":"same durable mission state is sufficient","considered_alternatives":[],"tasks":[]}'],
    )
    db = DurableTaskStore(tmp_path / "tasks.db")
    event_bus = DurableEventBus(db)
    failover_store = DirectorProviderFailoverStore(str(tmp_path / "tasks.db"))
    controller = DirectorProviderFailoverController(
        active_provider=gemini,
        candidates=(_candidate("nvidia_api", nvidia, "nvidia/test-model"),),
        store=failover_store,
        activate=lambda _candidate, _provider: None,
    )
    planner = DirectorPlanner(router=None, provider=controller)
    director = DurableDirectorService(
        planner=planner,
        task_engine=StubTaskEngine(),
        store=db,
        event_bus=event_bus,
    )

    submission = director.submit_mission(
        objective="continue one mission across Director provider failure"
    )
    task_ids_before = [task.task.task_id for task in db.list_tasks(submission.mission_id)]

    review = planner.review(
        objective=submission.plan.objective,
        context=[{"role":"user","content":"The durable mission still contains task-1 and it has not been duplicated."}],
    )
    task_ids_after = [task.task.task_id for task in db.list_tasks(submission.mission_id)]

    assert submission.mission_id
    assert task_ids_before == ["task-1"]
    assert task_ids_after == task_ids_before
    assert review.decision == "complete"
    assert controller.provider_id == "nvidia_api"
    assert any(
        row["phase"] == "succeeded" and row["to_provider"] == "nvidia_api"
        for row in failover_store.list_recent(limit=20)
    )
