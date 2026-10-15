from pathlib import Path

import pytest

from aurora.orchestration.event_bus import DurableEventBus, EventBusError
from aurora.orchestration.mission_memory import MailboxScope, MissionMemory
from aurora.orchestration.task_store import DurableTaskStore


def test_event_bus_publishes_and_filters_by_project_agent_and_correlation(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)

    project_event = bus.publish(
        mission_id="m1",
        event_type="task_started",
        payload={"task": "research"},
        project_id="project-a",
        agent_role="researcher",
        correlation_id="corr-1",
    )
    other_project = bus.publish(
        mission_id="m2",
        event_type="task_started",
        payload={"task": "other"},
        project_id="project-b",
        agent_role="reviewer",
        correlation_id="corr-2",
    )

    project_view = bus.subscribe("dashboard", project_id="project-a")
    agent_view = bus.subscribe("agent-console", agent_role="researcher")
    corr_view = bus.subscribe("trace", correlation_id="corr-1")

    assert [event.event_id for event in bus.poll(project_view)] == [project_event.event_id]
    assert [event.event_id for event in bus.poll(agent_view)] == [project_event.event_id]
    assert [event.event_id for event in bus.poll(corr_view)] == [project_event.event_id]

    event = bus.poll(project_view)[0]
    assert event.project_id == "project-a"
    assert event.agent_role == "researcher"
    assert event.correlation_id == "corr-1"
    assert other_project.event_id != event.event_id


def test_filtered_event_cursor_can_ack_over_unrelated_global_ids(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)

    first = bus.publish(
        mission_id="m1",
        event_type="project_event",
        project_id="project-a",
    )
    unrelated = bus.publish(
        mission_id="m2",
        event_type="other",
        project_id="project-b",
    )
    second = bus.publish(
        mission_id="m3",
        event_type="project_event",
        project_id="project-a",
    )

    view = bus.subscribe("dashboard", project_id="project-a")
    assert [event.event_id for event in bus.poll(view)] == [
        first.event_id,
        second.event_id,
    ]

    bus.ack(view, first.event_id)
    assert [event.event_id for event in bus.poll(view)] == [second.event_id]
    bus.ack(view, second.event_id)
    assert bus.poll(view) == ()

    assert unrelated.event_id > first.event_id


def test_filtered_event_cursor_cannot_skip_its_own_matching_event(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)

    first = bus.publish(
        mission_id="m1",
        event_type="project_event",
        project_id="project-a",
    )
    second = bus.publish(
        mission_id="m2",
        event_type="project_event",
        project_id="project-a",
    )

    view = bus.subscribe("dashboard", project_id="project-a")
    with pytest.raises(EventBusError, match="cannot skip matching events"):
        bus.ack(view, second.event_id)

    assert [event.event_id for event in bus.poll(view)] == [
        first.event_id,
        second.event_id,
    ]


def test_unfiltered_event_cursor_keeps_strict_no_skip_semantics(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)
    first = bus.publish(mission_id="m1", event_type="a")
    second = bus.publish(mission_id="m1", event_type="b")

    subscription = bus.subscribe("director")
    with pytest.raises(EventBusError, match="cannot skip events"):
        bus.ack(subscription, second.event_id)

    bus.ack(subscription, first.event_id)
    bus.ack(subscription, second.event_id)


def test_filtered_event_subscriptions_keep_independent_cursors(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    bus = DurableEventBus(store)

    event_a = bus.publish(
        mission_id="m1",
        event_type="a",
        project_id="project-a",
    )
    event_b = bus.publish(
        mission_id="m2",
        event_type="b",
        project_id="project-b",
    )
    event_c = bus.publish(
        mission_id="m3",
        event_type="c",
        project_id="project-a",
    )

    first_view = bus.subscribe("dashboard", project_id="project-a")
    second_view = bus.subscribe("dashboard", project_id="project-b")

    assert [event.event_id for event in bus.poll(first_view)] == [
        event_a.event_id,
        event_c.event_id,
    ]
    assert [event.event_id for event in bus.poll(second_view)] == [event_b.event_id]

    bus.ack(first_view, event_a.event_id)
    assert [event.event_id for event in bus.poll(first_view)] == [event_c.event_id]
    assert [event.event_id for event in bus.poll(second_view)] == [event_b.event_id]


def test_mission_memory_supports_scoped_mailbox_views_and_correlation(tmp_path: Path):
    store = DurableTaskStore(tmp_path / "tasks.db")
    memory = MissionMemory(store)

    global_message = memory.append(
        mission_id="m1",
        source="director",
        kind="notice",
        content="global notice",
        scope=MailboxScope.GLOBAL,
        correlation_id="corr-1",
    )
    project_message = memory.append(
        mission_id="m1",
        source="director",
        kind="project",
        content="project work",
        scope=MailboxScope.PROJECT,
        project_id="project-a",
        event_id=10,
        correlation_id="corr-1",
    )
    agent_message = memory.append(
        mission_id="m2",
        source="researcher",
        kind="result",
        content="agent result",
        scope=MailboxScope.AGENT,
        agent_role="researcher",
        task_id="task-1",
        correlation_id="corr-2",
    )
    task_message = memory.append(
        mission_id="m3",
        source="worker",
        kind="trace",
        content="task trace",
        scope=MailboxScope.TASK,
        task_id="task-1",
        correlation_id="corr-2",
    )

    assert memory.list_global() == (
        global_message,
        project_message,
        agent_message,
        task_message,
    )
    assert memory.list_project("project-a") == (project_message,)
    assert memory.list_agent("researcher") == (agent_message,)
    assert memory.list_task("task-1") == (agent_message, task_message)
    assert memory.list_correlation("corr-2") == (agent_message, task_message)

    assert project_message.event_id == 10
    assert project_message.project_id == "project-a"
    assert agent_message.scope is MailboxScope.AGENT
    assert task_message.scope is MailboxScope.TASK


def test_mission_memory_validates_scope_identity_requirements(tmp_path: Path):
    memory = MissionMemory(DurableTaskStore(tmp_path / "tasks.db"))

    with pytest.raises(Exception, match="project scope requires project_id"):
        memory.append(
            mission_id="m1",
            source="director",
            kind="project",
            content="missing project",
            scope=MailboxScope.PROJECT,
        )

    with pytest.raises(Exception, match="agent scope requires agent_role"):
        memory.append(
            mission_id="m1",
            source="director",
            kind="agent",
            content="missing agent",
            scope=MailboxScope.AGENT,
        )

    with pytest.raises(Exception, match="task scope requires task_id"):
        memory.append(
            mission_id="m1",
            source="director",
            kind="task",
            content="missing task",
            scope=MailboxScope.TASK,
        )


def test_event_and_mailbox_metadata_share_one_durable_database(tmp_path: Path):
    path = tmp_path / "tasks.db"
    store = DurableTaskStore(path)
    bus = DurableEventBus(store)
    memory = MissionMemory(store)

    event = bus.publish(
        mission_id="m1",
        event_type="evidence_observed",
        project_id="project-a",
        agent_role="researcher",
        correlation_id="corr-7",
    )
    message = memory.append(
        mission_id="m1",
        source="event-projector",
        kind="event_projection",
        content="evidence observed",
        scope=MailboxScope.PROJECT,
        project_id="project-a",
        event_id=event.event_id,
        correlation_id="corr-7",
    )

    with store._connect() as db:
        tables = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "task_events" in tables
        assert "mission_messages" in tables
        assert not any("mailbox" in table or "journal" in table for table in tables)

    assert message.event_id == event.event_id
    assert memory.list_correlation("corr-7") == (message,)


def test_scope_metadata_survives_restart(tmp_path: Path):
    path = tmp_path / "tasks.db"
    memory = MissionMemory(DurableTaskStore(path))
    saved = memory.append(
        mission_id="m1",
        source="director",
        kind="trace",
        content="persisted",
        scope=MailboxScope.PROJECT,
        project_id="project-a",
        task_id="task-1",
        event_id=3,
        correlation_id="corr-9",
    )

    reopened = MissionMemory(DurableTaskStore(path))
    assert reopened.list_project("project-a") == (saved,)
    assert reopened.list_task("task-1")[0].correlation_id == "corr-9"



def test_mission_memory_first_of_kind_preserves_evolved_metadata(tmp_path: Path):
    memory = MissionMemory(DurableTaskStore(tmp_path / "tasks.db"))
    saved = memory.append(
        mission_id="m1",
        source="director",
        kind="objective",
        content="objective",
        scope=MailboxScope.PROJECT,
        project_id="project-a",
        agent_role="director",
        task_id="task-1",
        event_id=7,
        correlation_id="corr-8",
    )

    assert memory.first_of_kind("m1", "objective") == saved



def test_mission_memory_migrates_legacy_message_table(tmp_path: Path):
    path = tmp_path / "tasks.db"
    with store_connection(path) as db:
        db.executescript(
            """
            CREATE TABLE mission_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        db.execute(
            """
            INSERT INTO mission_messages (
                mission_id, source, kind, content, metadata_json, created_at
            ) VALUES ('m1', 'director', 'legacy', 'old message', '{}',
                      '2026-10-01T10:00:00+00:00')
            """
        )

    memory = MissionMemory(DurableTaskStore(path))
    messages = memory.list("m1")
    assert len(messages) == 1
    assert messages[0].scope is MailboxScope.MISSION
    assert messages[0].project_id is None
    assert messages[0].correlation_id is None


def store_connection(path: Path):
    import sqlite3

    return sqlite3.connect(path)


def test_mission_memory_is_idempotent_for_replayed_event_ids(tmp_path: Path):
    from aurora.orchestration.mission_memory import MissionMemoryError

    store = DurableTaskStore(tmp_path / "tasks.db")
    memory = MissionMemory(store)
    kwargs = {
        "mission_id": "mission-replay",
        "source": "skill-learning",
        "kind": "skill.evaluated",
        "content": '{"skill_id": "research", "score": 0.9}',
        "metadata": {"event_id": 51, "task_id": "task-1"},
        "event_id": 51,
        "task_id": "task-1",
    }

    first = memory.append(**kwargs)
    replay = memory.append(**kwargs)

    assert replay.message_id == first.message_id
    assert len([item for item in memory.list("mission-replay") if item.event_id == 51]) == 1

    with pytest.raises(MissionMemoryError, match="already bound to a different mailbox projection"):
        memory.append(**{**kwargs, "content": '{"skill_id": "research", "score": 0.1}'})
