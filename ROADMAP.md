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
shared mission memory/inbox, persisted task results, and adaptive Director
review/replanning across durable mission phases.

## Phase 4
Failure injection, security regression, observability, recovery drills and controlled live authorization.