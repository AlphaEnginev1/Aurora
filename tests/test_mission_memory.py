from pathlib import Path
import sqlite3

import pytest

from aurora.orchestration.event_bus import DurableEventBus
from aurora.orchestration.mission_memory import MAILBOX_EVENT_TYPE, MissionMemory, MissionMemoryError
from aurora.orchestration.task_store import DurableTaskStore


def test_mission_memory_is_backed_by_single_event_journal(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    memory = MissionMemory(store)

    message = memory.append(
        mission_id="mission-1",
        source="director",
        kind="review",
        content="Inspect the research result.",
        metadata={"round": 1},
        project_id="project-a",
        agent_role="researcher",
        task_id="task-1",
        correlation_id="corr-1",
        causation_id="event-7",
    )

    events = store.events("mission-1")
    assert len(events) == 1
    assert events[0].event_type == MAILBOX_EVENT_TYPE
    assert events[0].event_id == message.message_id
    assert events[0].project_id == "project-a"
    assert events[0].agent_role == "researcher"
    assert events[0].correlation_id == "corr-1"
    assert events[0].causation_id == "event-7"


def test_mission_memory_exposes_global_project_agent_and_correlation_views(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    memory = MissionMemory(store)

    memory.append(
        mission_id="mission-1",
        source="director",
        kind="project-note",
        content="Project A note",
        project_id="project-a",
        agent_role="researcher",
        correlation_id="corr-a",
    )
    memory.append(
        mission_id="mission-2",
        source="agent",
        kind="agent-note",
        content="Project B note",
        project_id="project-b",
        agent_role="builder",
        correlation_id="corr-b",
    )

    assert [m.content for m in memory.list_global()] == [
        "Project A note",
        "Project B note",
    ]
    assert [m.content for m in memory.list_project("project-a")] == ["Project A note"]
    assert [m.content for m in memory.list_agent("researcher")] == ["Project A note"]
    assert [m.content for m in memory.list_correlation("corr-b")] == ["Project B note"]


def test_event_bus_view_reads_same_authoritative_journal(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)

    bus.publish(
        mission_id="mission-1",
        event_type="task.progress",
        payload={"step": 1},
        project_id="project-a",
        agent_role="researcher",
        correlation_id="corr-1",
    )
    bus.publish(
        mission_id="mission-2",
        event_type="task.progress",
        payload={"step": 2},
        project_id="project-b",
        agent_role="builder",
        correlation_id="corr-2",
    )

    project_events = bus.view(project_id="project-a")
    assert len(project_events) == 1
    assert project_events[0].payload["step"] == 1

    agent_events = bus.view(agent_role="builder")
    assert len(agent_events) == 1
    assert agent_events[0].project_id == "project-b"


def test_legacy_mission_messages_are_migrated_once(tmp_path: Path):
    path = tmp_path / "tasks.db"
    store = DurableTaskStore(path)

    with store._connect() as db:
        db.execute(
            """
            CREATE TABLE mission_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO mission_messages (
                mission_id, source, kind, content, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-mission",
                "director",
                "legacy",
                "migrated content",
                '{"legacy": true}',
                "2026-10-01T10:00:00+00:00",
            ),
        )

    memory = MissionMemory(DurableTaskStore(path))
    messages = memory.list("legacy-mission")

    assert len(messages) == 1
    assert messages[0].content == "migrated content"
    assert messages[0].metadata == {"legacy": True}

    with DurableTaskStore(path)._connect() as db:
        assert (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mission_messages'"
            ).fetchone()
            is None
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM task_events WHERE event_type = ?",
                (MAILBOX_EVENT_TYPE,),
            ).fetchone()[0]
            == 1
        )


def test_mailbox_rejects_invalid_context_metadata(tmp_path: Path):
    memory = MissionMemory(DurableTaskStore(tmp_path / "tasks.db"))
    with pytest.raises(MissionMemoryError, match="project_id contains"):
        memory.append(
            mission_id="mission-1",
            source="director",
            kind="note",
            content="content",
            project_id="bad\x00project",
        )
