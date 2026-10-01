# Aurora Dashboard Gateway / Read Model

Block 9 establishes the authoritative read boundary between Aurora runtime state and the future dashboard UI.

## Contract

`DashboardGateway` is read-only and JSON-serializable.

It composes:

- Project Registry
- Organization Registry
- Skill Registry and learning store
- Provider Registry
- Model Registry and model-performance store
- durable task/event store
- shared MissionMemory mailbox projection
- research knowledge/evidence store
- Director bootstrap context and policy
- decision performance and decision knowledge
- observability metadata

The gateway does not mutate these sources and does not contain provider/model business logic.

## Read surfaces

`snapshot()` provides the complete dashboard bootstrap/read-model view.

`project(project_id)` provides project inspector data.

`agent(role)` provides agent inspector data.

`mailbox(...)` provides scoped event and mailbox views.

`model_intelligence(...)`, `skills_learning(...)`, `research(...)` and `governance(...)` expose the corresponding dashboard domains.

Missing optional stores are represented explicitly as `unknown` rather than inferred.

## Security boundary

The dashboard layer receives data through the gateway. It does not receive direct database write primitives or provider factories.

Mutation/command authorization remains outside this read model and must pass through Aurora governance/runtime boundaries.

## Live boundary

Block 9 is synchronous read aggregation.

Event-driven synchronization and snapshot/event reconciliation remain Block 11.