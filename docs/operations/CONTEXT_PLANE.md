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

The current Block 2 implementation establishes the contract and composition layer. It intentionally does not yet make the gate mandatory for every inference call. That is Block 4, after Director Bootstrap (Block 3).
