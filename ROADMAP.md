# Aurora Roadmap

## Current Gate — CONSOLIDATION FREEZE ACTIVE

Aurora is temporarily frozen against new functionality while the repository is reconciled into
one authoritative implementation line.

Authoritative consolidation record:
- `docs/AURORA_CONSOLIDATION_MATRIX.md`
- Issue #50
- Audit branch: `runtime/consolidation-gate`
- Implementation baseline: `runtime/increment-10-director-contract`
- Verified baseline commit: `64193c0cbbaccc0920c87f20054c83bc0c482d556`
- CI 1287, 1288 and 1289: SUCCESS

During this gate, only documentation, verification, tests, and fixes required for already-existing
behavior are allowed.

## Phase 0 — Foundation / Governance — INTEGRATED

Repository architecture, governance, security policy, schemas, CI and core operational records exist.

## Phase 1 — Autonomous Capability Acquisition — INTEGRATED

Implemented:
- capability registry and broker;
- governed capability discovery and persistent acquisition;
- APT/PyPI acquisition paths;
- artifact verification/provenance;
- dependency closure resolution;
- transactional state, staging, recovery and reconciliation;
- tool artifact injection.

Known limitation:
- npm/GitHub-release sources remain registered but are not executable acquisition paths.
- OS-level transparent network enforcement remains separate from application-level gateway policy.

## Phase 2 — Runtime Safety / Persistence / Recovery — INTEGRATED WITH HARDENING GAP

Implemented:
- sandboxed execution;
- deterministic isolated workspaces;
- network gateway/proxy/dataplane;
- durable task/event state;
- leases, crash recovery and reconciliation;
- bounded concurrency and shared-resource locking.

Remaining verification/hardening:
- stronger host/OS-level enforcement and production recovery drills.

## Phase 3 — Director / Organization / Mission Runtime — INTEGRATED

Implemented:
- provider-neutral Director planning/review;
- durable TaskEngine;
- 24/7 DirectorRuntime and daemon;
- shared mission memory/inbox;
- bounded adaptive review/replan;
- organization and department registry;
- specialist team formation and adaptation.

Mission loop safety:
- bounded Director review rounds;
- bounded `run_until_terminal` cycles;
- durable state and restart recovery;
- no evidence leakage from later decisions into earlier decision evaluations.

## Phase 8 — Adaptive Agent Intelligence — CLOSED

Closed at merge commit:
`d5621d08e0207eaa222620f3d6fc7f7f016b0edf`

Implemented:
- specialist skill evaluation;
- durable skill learning;
- trusted model performance history;
- dynamic model routing;
- evidence-backed skill evolution;
- explicit approval/promotion;
- validation, regression and rollback;
- adaptive specialist-agent selection.

## Phase 9 — Knowledge & Research Intelligence — IMPLEMENTED THROUGH VERIFIED ADAPTIVE ROUTING PROOF

Implemented:
1. research domain and durable evidence;
2. redirect-safe provenance;
3. deterministic source parsing and claim extraction;
4. independent verification and contradiction handling;
5. trusted knowledge artifacts and citation graph;
6. Director/task-engine research delegation;
7. trust-gated research → learning bridge;
8. verified execution evidence changing a later ModelRouter decision.

Proof references:
- research-to-learning bridge: CI 1069;
- adaptive routing proof: CI 1075;
- provider-bound execution contract: subsequent Increment 9 work;
- Director decision-learning/policy lifecycle: CIs 1193–1289.

Important distinction:
- the adaptive routing proof used controlled/test providers;
- dedicated live NVIDIA and live Gemini workflows exist but are opt-in/manual and are not, by themselves,
  proof of continuously verified production operation.

### Research capability status

The current repository contains an `HttpSearchProvider` and governed web-discovery code.
However, the current runtime factory does not wire that provider into `ResearchService`.
Therefore autonomous research discovery is classified as **PARTIALLY INTEGRATED**, not closed.

## Cross-cutting Director Decision Intelligence — INTEGRATED

Implemented and verified in the current line:
- decision outcome/evidence sufficiency;
- durable decision performance;
- explicit comparative alternatives;
- independent comparative evidence;
- repeated and cross-decision replication summaries;
- promoted empirical knowledge;
- governed policy proposals;
- policy validation and canary lifecycle;
- shadow canary evaluation and rollback;
- active policy registry;
- immutable typed active policy contracts.

The policy path remains advisory and explicitly re-evaluates current evidence; it does not become an
automatic decision rule.

## Live Provider Verification — NOT YET ESTABLISHED

Dedicated manual/opt-in gates exist for:
- NVIDIA adaptive learning;
- Gemini Director.

The consolidation gate must classify any live result with its exact run evidence. No live run is inferred
from the existence of a workflow.

## Genuine Remaining Work After Consolidation

These are not active implementation tickets yet. They become eligible only after Issue #50 closes:

1. Reconcile `main` with the canonical implementation branch and select one permanent source of truth.
2. Close/archive obsolete stacked PRs and historical branches or explicitly classify them as archival.
3. Finish/verify the governed Research discovery runtime integration.
4. Decide and verify OS-level network enforcement requirements.
5. Execute and record live provider gates where production verification is required.
6. Complete production observability, recovery drills and controlled live authorization.
7. Only then define the next net-new capability increment from an evidence-backed gap.

No item above should be reimplemented if an existing module already satisfies it; the consolidation matrix
must be checked first.
