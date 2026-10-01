# Aurora Shared Mailbox / Event Bus Evolution

Block 8 evolves the existing durable Event Bus and MissionMemory into scoped mailbox projections without introducing another queue, journal or persistence system.

## Single durable source

`DurableTaskStore` remains the SQLite source of truth for tasks, events and consumer cursors.

`MissionMemory` remains in the same SQLite database as a durable mailbox/context projection. It is not a second event journal.

## Event correlation

Events can now carry:

- `project_id`
- `agent_role`
- `correlation_id`

This metadata enables dashboard drill-down and filtered runtime views without encoding identity only inside arbitrary payload dictionaries.

Existing event rows remain valid; the schema is migrated in place when the store opens.

## Event Bus views

`DurableEventBus.subscribe()` can create filtered views by:

- mission
- project
- agent
- correlation

Each filtered view receives a deterministic independent consumer cursor. This prevents one dashboard or specialist view from advancing another view's cursor.

Unfiltered subscriptions keep the original strict sequential ACK semantics.

Filtered subscriptions may ACK the next matching event even when unrelated global event IDs exist between matching events. This is controlled by the view-specific cursor and does not permit a filtered view to skip one of its own matching events.

## Mailbox views

`MissionMemory` now supports cross-scope projections:

- global
- project
- agent
- task
- correlation

Each message retains scope and correlation metadata plus optional event/task/project/agent identifiers.

Legacy mission-scoped `list()` and `context()` remain compatible.

## Boundary

No duplicate queue or journal is introduced.

Dashboard Gateway and Read Model remain Block 9. This block provides the durable contracts and views that the dashboard will consume.