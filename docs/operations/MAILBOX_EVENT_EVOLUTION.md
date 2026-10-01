# Aurora Mailbox + Event Journal Evolution

## Contract

Aurora has one durable event journal: `task_events` in `DurableTaskStore`.

`MissionMemory` is a compatibility and context facade over that journal. It does
not maintain a second mailbox/message journal.

Mailbox messages are stored as event type `mailbox.message` with payload:

- `source`
- `kind`
- `content`
- `metadata`

Event rows also carry optional contextual metadata:

- `project_id`
- `agent_role`
- `task_id`
- `correlation_id`
- `causation_id`

This lets Aurora correlate a dashboard view back to a concrete mission, task,
execution or causal event without duplicating durable state.

## Read views

The authoritative journal exposes filtered views through `DurableTaskStore.events_view`
and `DurableEventBus.view`:

- global: no contextual filter
- project: `project_id`
- agent: `agent_role`
- task: `task_id`
- correlation: `correlation_id`
- mission/event type: additional filters

`MissionMemory` exposes the corresponding mailbox-oriented helpers:

- `list_global()`
- `list_project(project_id)`
- `list_agent(agent_role)`
- `list_task(task_id)`
- `list_correlation(correlation_id)`
- `list(mission_id)`

All views read the same event rows.

## Migration

Older installations may contain the pre-unification `mission_messages` table.
When `MissionMemory` initializes, it migrates those rows into
`task_events` as `mailbox.message` events and removes the old table within the
same SQLite transaction.

The migration preserves message content, metadata and original creation time.
Legacy rows receive a `legacy_message_id` in their event payload for auditability.

## Boundary

This block does not create dashboard persistence or a separate queue. The
Dashboard Gateway must read Aurora's authoritative stores/runtime and must not
introduce another source of truth.
