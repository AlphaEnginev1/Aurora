# Aurora Roadmap

## Canonical status

**Canonical engineering branch: `main`**

Authoritative status:
- `docs/AURORA_CONSOLIDATION_MATRIX.md`
- Issue #50
- consolidation evidence branch: `runtime/consolidation-gate`
- CI 1291–1296: SUCCESS

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

Remaining production boundary:
- additional acquisition source families where required;
- host/OS network enforcement.

## Phase 2 — Runtime Safety / Persistence / Recovery — INTEGRATED

Integrated:
- sandboxed execution;
- deterministic isolated workspaces;
- application network gateway/proxy/dataplane;
- durable task/event state;
- leases, crash recovery and reconciliation;
- bounded concurrency and shared-resource locking.

Remaining production verification:
- production-grade observability;
- controlled recovery drills;
- host/OS-level enforcement proof if required.

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

## Phase 9 — Knowledge & Research Intelligence — INTEGRATED WITH EXPLICIT DISCOVERY WIRING GAP

Integrated:
1. research domain and durable evidence;
2. redirect-safe provenance;
3. deterministic source parsing and claim extraction;
4. independent verification and contradiction handling;
5. trusted knowledge artifacts and citation graph;
6. Director/TaskEngine research delegation;
7. trust-gated research → learning;
8. verified execution evidence affecting later ModelRouter decisions.

The existing `HttpSearchProvider` is implemented and tested, but the normal runtime factory does not yet inject
it into `ResearchService`. This is a **runtime integration gap**, not a missing research architecture.

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

## Live Provider Verification — NOT YET ESTABLISHED

Dedicated opt-in workflows exist for:
- NVIDIA adaptive learning;
- Gemini Director.

A workflow definition does not count as a live verification. The exact successful run must be executed and recorded.

## Production readiness work remaining

These are the only current production-oriented gaps identified by the consolidation audit:

1. **Research discovery runtime wiring:** connect the existing governed `HttpSearchProvider` through runtime configuration
   and verify the end-to-end Director research-discovery path.
2. **OS-level network enforcement:** decide the required host boundary and execute the proof where required.
3. **Live NVIDIA verification:** execute and archive real-provider evidence.
4. **Live Gemini verification:** execute and archive real-provider evidence.
5. **Production observability:** complete operational visibility and alerting evidence.
6. **Recovery drills:** execute controlled crash/restart/reconciliation exercises and archive results.
7. **Controlled production authorization:** establish the explicit live-operation gate.

## Increment policy

**Increment 11 is frozen.**

No new capability increment may be opened until Issue #50 is closed and the next increment is derived from the
remaining evidence-backed production gap list.
