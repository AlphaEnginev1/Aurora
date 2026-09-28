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

Specialist skill foundation is implemented: versioned skill registry, default
agent specializations, Director task-level skill assignment, capability compatibility
validation, model preference contribution, specialist context injection, durable skill
persistence and replan preservation.

## Phase 8 — Adaptive Agent Intelligence — CLOSED

Phase 8 is closed on merge commit `d5621d08e0207eaa222620f3d6fc7f7f016b0edf`.

Delivered:
- skill evaluation with explicit evidence completeness/state;
- durable skill learning history;
- trusted model/agent performance history;
- bounded evidence-backed skill evolution proposals with explicit approval;
- dynamic model routing using skill/work affinity, context fit, model quality/latency/cost,
  preferences and trusted learned performance;
- auditable routing decisions;
- automatic specialist-agent selection from required skills and capabilities;
- deterministic/fail-closed selection behavior;
- runtime feedback from executed tasks into skill learning.

Active skills are never silently mutated by learning, and incomplete evidence is
never promoted to trusted routing history.

## Cross-cutting Runtime Hardening — IN PROGRESS

Parallel agent execution is now treated as a transversal runtime capability:
- bounded concurrent durable workers can claim independent tasks atomically;
- execution attempts receive isolated deterministic workspaces;
- acquisition mutations are serialized at the shared installation target;
- append-only audit writes are serialized across concurrent workers;
- concurrency regression tests cover duplicate claims, workspace isolation and acquisition locking.

This hardening must pass CI and runtime stress validation before being declared production-verified.

### Mission Team Runtime — IMPLEMENTED

The operational team layer is now connected across the mission lifecycle:
- durable mission submission forms a specialist team from the task DAG's required skills;
- automatic task roles are resolved only within that formed team;
- explicit assignments outside the team fail closed;
- the existing DAG remains the execution authority, preserving dependency barriers and durable worker fencing;
- worker execution continues to feed skill evaluation, trusted learning and bounded skill-evolution proposals;
- task execution continues to feed dynamic model routing through skill/work context and trusted model history;
- team state is emitted as durable mission events and supplied to Director review context;
- Director follow-up phases reform the team before new tasks enter the durable queue.

PR #27 merged as 30239c0c78b858996054e5d5bfc7503b7e307260; CI run 769 passed both the full unit suite and e2e-network suite.

## Phase 9 — Knowledge & Research Intelligence — ACTIVE

Objective: give Aurora an evidence-first research capability that can investigate
the external world through governed network access and produce durable, auditable
knowledge.

Initial implementation:
- research request/domain models;
- source provenance records with content hashes and retrieval timestamps;
- durable evidence storage;
- fail-closed evidence states;
- governed retrieval through the existing InternetGateway;
- tests for evidence completeness and provenance.

Completed increments:
1. retrieval metadata and redirect-safe provenance;
2. source parsing and claim extraction — implemented in PR #48 with deterministic visible-text parsing, stable provenance-bound candidate claims, durable claim storage and fail-closed parsing tests.
3. independent evidence verification and contradiction handling — implemented in the verification increment with explicit independent-source requirements, contradiction precedence, durable evidence states and fail-closed verification tests.
4. trusted knowledge artifacts and citation graph — implemented on the current branch with verified-only artifact creation, immutable source-content provenance, durable citation edges and fail-closed trust-boundary tests.
5. Director/task-engine research delegation — implemented on the current branch with a first-class durable `research` task path through the governed ResearchService, bounded source/claim result summaries, and task-engine coverage.
6. research results feeding skill learning and model routing — trust-gated on the current branch: retrieval/extraction remains non-learning, only VERIFIED knowledge artifacts can authorize adaptive feedback, and the existing skill/model routing stores remain the learning sinks.

Next increments:
7. verified research-to-learning execution bridge and end-to-end model-routing feedback.

## Phase 4
Failure injection, security regression, observability, recovery drills and controlled live authorization.
