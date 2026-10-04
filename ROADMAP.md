# Aurora Roadmap

## Canonical status

**Canonical engineering branch: `main`**

Authoritative status:
- `docs/AURORA_CONSOLIDATION_MATRIX.md`
- Issue #50 (CLOSED)
- consolidation evidence branch: `runtime/consolidation-gate`
- CI 1291–1296: SUCCESS
- CI 1305: SUCCESS — research discovery E2E
- CI 1306: SUCCESS — canonical main after merge
- CI 1392: SUCCESS — Production Observability V1
- CI 1398/1399: SUCCESS — Recovery Drills V1
- CI 1414: SUCCESS — Controlled Live Authorization V1 on canonical main

The repository is no longer tracking historical increments as future work. The sections below describe what is already integrated and what remains genuinely required.

## Phase 0 — Foundation / Governance — INTEGRATED

Repository architecture, governance, security policy, schemas, CI and operational records are integrated.

## Phase 1 — Autonomous Capability Acquisition — INTEGRATED

Integrated:
- capability registry and broker;
- governed capability discovery and persistent acquisition;
- APT/PyPI acquisition;
- artifact verification/provenance;
- dependency closure;
- transactional state, staging, recovery and reconciliation;
- tool artifact injection.

Remaining non-queue hardening:
- additional acquisition source families where required;

## Phase 2 — Runtime Safety / Persistence / Recovery — INTEGRATED

Integrated:
- sandboxed execution;
- deterministic isolated workspaces;
- application network gateway/proxy/dataplane;
- durable task/event state;
- leases, crash recovery and reconciliation;
- bounded concurrency and shared-resource locking.

Production verification for the frozen queue is complete:
- production observability — VERIFIED;
- controlled recovery drills — VERIFIED;
- OS/network enforcement — VERIFIED.

## Phase 3 — Director / Organization / Mission Runtime — INTEGRATED

Integrated:
- provider-neutral Director planning/review;
- durable TaskEngine;
- 24/7 DirectorRuntime and daemon;
- shared mission memory/inbox;
- bounded adaptive review/replan;
- organization/departments;
- specialist team formation and adaptation.

Loop safety is established by bounded per-mission review/cycle budgets and durable restart state.

## Phase 8 — Adaptive Agent Intelligence — CLOSED

Integrated and verified:
- specialist skill evaluation;
- durable skill learning;
- model performance history;
- dynamic model routing;
- evidence-backed skill evolution;
- approval/promotion;
- validation, regression and rollback;
- adaptive specialist-agent selection.

## Phase 9 — Knowledge & Research Intelligence — INTEGRATED

Integrated:
1. research domain and durable evidence;
2. redirect-safe provenance;
3. deterministic source parsing and claim extraction;
4. independent verification and contradiction handling;
5. trusted knowledge artifacts and citation graph;
6. Director/TaskEngine research delegation;
7. trust-gated research → learning;
8. verified execution evidence affecting later ModelRouter decisions.

The existing `HttpSearchProvider` is now injected by the normal runtime factory when `AURORA_RESEARCH_DISCOVERY_ENDPOINT` is configured. `DirectorRuntime.acquire_specialist()` reaches this path through `SkillDiscoveryService`, which delegates source discovery to `ResearchService.discover()`. CI 1305 provides the controlled HTTP end-to-end proof and CI 1306 verifies the merged canonical `main`.

The adaptive routing proof used controlled/test providers. Live NVIDIA and Gemini verification remain separate gates.

## Cross-cutting Director Decision Intelligence — INTEGRATED

Integrated and verified:
- decision outcome/evidence sufficiency;
- durable performance aggregation;
- explicit comparative alternatives;
- independent comparative evidence;
- cross-decision replication;
- promoted empirical knowledge;
- governed policy proposals;
- validation and canary lifecycle;
- shadow canary evaluation/rollback;
- active policy registry;
- immutable typed active policy contracts.

## Live Provider Verification — VERIFIED

The dedicated live-provider evidence gates are closed:
- LIVE-RESEARCH-DISCOVERY — VERIFIED;
- LIVE-NVIDIA — VERIFIED;
- LIVE-GEMINI — VERIFIED.

## Frozen Production Readiness — COMPLETE

Blocks 1–7 of the ordered production-readiness queue are VERIFIED on canonical `main`:
1. LIVE-RESEARCH-DISCOVERY
2. OS-NETWORK-ENFORCEMENT
3. LIVE-NVIDIA
4. LIVE-GEMINI
5. PRODUCTION-OBSERVABILITY
6. RECOVERY-DRILLS
7. CONTROLLED-LIVE-AUTHORIZATION

The queue is closed. Block 7 establishes the explicit authorization boundary; it does not activate live execution automatically. Aurora remains fail-closed unless a human operator supplies a valid authorization record matching the verified canonical commit and prerequisites.

## Increment policy

**Increment 11 — Director Handoff / Failover: COMPLETE. Gates 1–8 are COMPLETE and canonically verified on main.**


## Increment 12 — Director Provider Failover / Replacement — COMPLETE / LIVE VERIFIED

**Scope:** provider-level replacement only. Increment 11 remains COMPLETE and is not reopened.

Target lifecycle:

    active Director provider -> provider failure -> eligible alternative -> replacement active -> same Aurora state -> continuation

Canonical implementation:
- PR #121 merged to main as `69edf62465e6aebea6a064d23b06efc3df38058d`;
- canonical post-merge CI #2104 green (`test` + `e2e-network`);
- durable provider failure/failover evidence store;
- provider-neutral failover controller;
- operator-configurable fallback order;
- Gemini failure classification for failoverable versus non-transient client errors;
- runtime integration for planning, review and durable conversation;
- successful replacement persisted for restart continuity;
- deterministic provider, mission, conversation, fail-closed and restart-persistence acceptance tests green;
- automatic switchback to a previously failed primary provider is not performed.

Acceptance plan:
- docs/operations/INCREMENT_12_DIRECTOR_PROVIDER_FAILOVER_CONTRACT.md
- docs/operations/INCREMENT_12_DIRECTOR_PROVIDER_FAILOVER_ACCEPTANCE_PLAN.md

Deterministic proof: VERIFIED on canonical main.

External live proof:
- workflow: `.github/workflows/live-director-provider-failover.yml`;
- test: `tests/e2e/test_live_director_provider_failover.py`;
- environment: `director-provider-failover-live`;
- live run: GitHub Actions run `37179243481` (job `111368251139`);
- primary: real Gemini `gemini-3.7-flash`;
- replacement: real NVIDIA provider using the NVIDIA served model supplied to the gate;
- result: SUCCESS — real primary response, controlled Gemini failure, real NVIDIA replacement response, provider transition evidence recorded;
- credential leakage: none observed in the gate output.

**Increment 12 acceptance: CLOSED / VERIFIED.**

No live authorization is activated by the gate.

Scope remains isolated to safe replacement and recovery of an active Director during an in-progress durable mission. Unrelated dashboard, model, provider, research, and frozen production-readiness work remains out of scope.



## Universal Work / Cowork Architecture — DESIGN FROZEN

The next architectural direction is frozen in:

`docs/architecture/AURORA_UNIVERSAL_WORKBENCH_ARCHITECTURE.md`

Status: **DESIGN FROZEN — IMPLEMENTATION NOT AUTHORIZED BY THIS DOCUMENT**.

The contract establishes a Universal Work / Cowork operating model in which the Director and all specialist agents use one governed work-capability plane. Required capability families include local computer/workspace access, file/artifact intake, browser and desktop interaction, GitHub and external connectors, MCP bridging, multimodal processing, capability discovery/acquisition/fallback, project intake/source graph, capability census/preflight, durable evidence/audit, and natural Director conversation.

This architectural direction does not reopen Increment 11, Increment 12, or the closed production-readiness queue. Implementation must be separately scoped and validated with deterministic tests plus live/end-to-end evidence where an external boundary is involved.
