# Aurora Roadmap

## Phase 0
Repository, architecture, governance, security, schemas, CI and tests.

## Phase 1
Capability broker, resolver, APT/PIP/NPM/Git acquisition, verification, audit, Internet policy and idempotent bootstrap.

## Phase 2
Sandbox, process manager, workspace isolation, network enforcement, persistence and recovery.

## Phase 3
Director, orchestrator, specialist agents, memory and task lifecycle.

Current implementation includes the durable task queue, Event Bus, worker leases,
shared mission memory/inbox, persisted task results, adaptive Director
review/replanning across durable mission phases, and the internal organization
registry with role/capability authorization and model-preference routing.

Specialist skill foundation is now implemented: versioned skill registry, default
agent specializations, Director task-level skill assignment, capability compatibility
validation, model preference contribution, specialist context injection, durable skill
persistence and replan preservation.

Skill learning/evolution now records per-skill execution evaluations, maintains
learned model performance, generates bounded pending evolution proposals from repeated
evidence, and feeds learned performance plus skill/work affinity into dynamic model
selection. Active skills are never silently mutated by this learning loop.

## Phase 4
Failure injection, security regression, observability, recovery drills and controlled live authorization.