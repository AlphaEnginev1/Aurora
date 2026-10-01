# Aurora Context Plane

The Context Plane is the authoritative context composition layer for Aurora.

## Purpose

It sits between Aurora state and model execution. Its job is to expose canonical, scoped and provenance-labelled context without making any model output authoritative.

The Context Plane does not replace:
- Project Registry
- Organization Registry
- Skill Registry
- Event Bus
- MissionMemory
- ResearchStore / trusted knowledge
- task persistence
- ModelRouter

It composes their authoritative indexes and later will provide governed retrieval into the Director and specialist model execution paths.

## Context contract

Every manifest identifies:
- scope
- project identity when applicable
- authoritative fragments
- provenance
- capture timestamp
- explicit unknown/missing fields

Unknown state is represented as `unknown`; the system never fills an absent authoritative value from model inference.

## Context Gate

`ContextGate.prepare()` is the fail-closed entry point.

A project-scoped request must resolve a registered project. Global/Director context must have the core Aurora catalogs. Mailbox and knowledge sources are required by default but may only be marked optional by an explicit caller policy during staged integration.

## Integration boundary

Block 2 established the contract and composition layer.

Block 3 loads a fail-closed startup snapshot through the gate before the runtime is returned.

Block 4 makes the gate the common inference prerequisite at `ModelRouter.generate()`. Production Director and specialist executions use that router, so provider selection cannot occur until authoritative context has been prepared.

Project-specific inference uses `project_id` metadata; executions without a project identity receive the global Aurora scope. The verified context manifest id is attached to inference metadata for auditability.
