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

The repository is no longer tracking historical increments as future work. The sections below describe what is
already integrated and what remains genuinely required.

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

**Increment 11 — Director Handoff / Failover: AUTHORIZED (contract + E2E proof phase).**

Scope is deliberately isolated to replacement of an active Director during an in-progress durable mission. This phase establishes the contract and deterministic E2E acceptance proof before any production handoff/election implementation. Unrelated dashboard, model, provider, research, and frozen production-readiness work remains out of scope.

The authoritative contract is `docs/operations/INCREMENT_11_DIRECTOR_HANDOFF_FAILOVER_CONTRACT.md`.


## Block 13 — Dashboard Full E2E — COMPLETE

The integrated Dashboard/Director/avatar proof is closed on canonical `main`.

Verified proof lanes:
1. 13A — integrated conversation lifecycle — VERIFIED.
2. 13B — voice-to-Director handoff and text fallback — VERIFIED.
3. 13C — conversation persistence across dashboard restart/reconciliation — VERIFIED.
4. 13D — canonical Aurora portrait HTTP serving plus operator visual acceptance — VERIFIED.
5. 13E — real Gemini provider gate — VERIFIED; the final local E2E run passed all 4 tests using Gemini 3.8 Flash.
6. 13F — final acceptance — COMPLETE.

Dashboard proof remains distinct from the frozen production-readiness queue 1–7. Live execution is still fail-closed and requires the existing explicit human authorization boundary.

**Increment 11 remains unapproved.**
