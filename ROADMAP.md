# Aurora Roadmap

## Canonical status

**Seven-point acceptance audit refreshed (2026-10-13), main baseline `a87e4bce7634afef0e3543b540d7cce49fec9799`.** Historical sections below preserve evidence for their original commits; this current snapshot takes precedence where statuses differ.

- **Point 1 — NVIDIA:** real primary inference succeeded on the prior PR #216 head ([run 38433386028](https://github.com/AlphaEnginev1/Aurora/actions/runs/38433386028)). PR #221 is merged and adds a manual-only live reserve drill: it first requires real primary inference, injects a *controlled* HTTP 503 for that primary in the recovery phase, then requires a real response from a different official NVIDIA Free Endpoint candidate. It has passed deterministic CI, but **the reserve drill has not yet been run manually**. This is not evidence of a natural NVIDIA upstream primary failure.
- **Point 2 — autonomy controllers:** CLOSED for deterministic canonical-runtime integration. PR #218's joint factory-level E2E passed both `test` and `e2e-network` ([run 38449077652](https://github.com/AlphaEnginev1/Aurora/actions/runs/38449077652)). Physical computer I/O and user's local WSL are separate.
- **Point 3 — branches:** the latest fetched branch snapshot had **240 refs including `main` (239 non-main)**. The prior full ancestry classification covered 231 non-main refs: 144 had zero commits ahead and 87 had commits ahead. The eight later-created refs were compared to current main and all had commits ahead, so the updated ancestry counts are 144 zero-ahead and 95 ahead/diverged. This is an ancestry count, not yet a semantic review of every divergent diff. No branch was deleted.
- **Point 4 — protection:** GitHub currently reports `main protected=false`, the accessible rulesets collection is empty, and the classic protection read is denied to the connected integration (HTTP 403). **Not closed.** Requires repository admin settings.
- **Point 5 — documentation:** this roadmap and the consolidation matrix are now reconciled with the latest evidence; full details are in the current acceptance report.
- **Point 6 — WSL:** clean local restart and real Director conversation remain pending on the user's machine. CI and a dashboard HTTP 200 are not enough.
- **Point 7 — providers:** OpenRouter is manual-only, with a fail-closed quota preflight merged in PR #220. The last confirmed free-quota reading was `used=51, limit=50, remaining=0`; a real OpenRouter completion remains pending until a later preflight observes positive allowance. PR #223's official NVIDIA Free Endpoint catalog parser fix and PR #224's secret-safe HTTP diagnostics are merged. All live provider workflows are manual-only; no paid fallback is allowed.

See `docs/operations/CURRENT_RUNTIME_ACCEPTANCE_2026-10-13.md` and `docs/audits/CURRENT_ACCEPTANCE_AND_BRANCH_INVENTORY_2026-10-13.md` for steps and evidence links.


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

## Current live-provider verification — NOT CLOSED (reconciled 2026-10-13)

Historical live runs are retained as evidence of earlier runtime states, not as certification of the latest main SHA.

- The older main acceptance runs [37814164201](https://github.com/AlphaEnginev1/Aurora/actions/runs/37814164201) and [37813096077](https://github.com/AlphaEnginev1/Aurora/actions/runs/37813096077) failed with HTTP 403 during actual NVIDIA inference.
- The open provider-settings PR #216 reports two successful live NVIDIA inference runs on its PR head; the post-PR-218 canonical main SHA has not yet had that dedicated live gate rerun.
- The OpenRouter authenticated key/quota preflight reports 51 requests used against a daily limit of 50. That probe succeeded for authentication/model listing and deliberately sent no chat completion after the remaining allowance was zero.
- PR #217 made the OpenRouter live inference workflow manual-only to prevent routine pull-request updates from spending the free allowance.
- Current multi-provider/reserve-pool LIVE acceptance and current WSL clean-start/Director-conversation acceptance remain pending.

Do not mark the active provider topology LIVE VERIFIED from ordinary CI, historical Gemini-to-NVIDIA evidence, or a live run on a different PR head. Current evidence and boundaries are tracked in `docs/operations/CURRENT_RUNTIME_ACCEPTANCE_2026-10-13.md`.

## Frozen historical production-readiness queue — COMPLETE AT ITS VERIFIED BASELINE

Blocks 1–7 were verified on their respective historical canonical commits. That historical closure is not evidence that the current provider configuration is live-ready. The current NVIDIA-only live gate remains blocked as stated above. The historical queue was:
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

**Increment 12 historical acceptance: CLOSED / VERIFIED for the Gemini-to-NVIDIA topology tested in run 37179243481. This does not verify the current NVIDIA-only runtime.**

No live authorization is activated by the gate.

Scope remains isolated to safe replacement and recovery of an active Director during an in-progress durable mission. Unrelated dashboard, model, provider, research, and frozen production-readiness work remains out of scope.



## Universal Work / Cowork Architecture — IMPLEMENTED AND VERIFIED

The Universal Workbench & Integration Plane is implemented on canonical `main` and has completed final Director-led end-to-end acceptance. The acceptance record is maintained in `docs/architecture/AURORA_UNIVERSAL_WORKBENCH_ARCHITECTURE.md`.

The Workbench provides the governed shared capability plane for local files/computer, artifacts/documents/images, browser, GitHub, external connectors, MCP, research, multimodal processing, code/tool execution, capability discovery/acquisition/fallback and durable evidence/audit.

It does not reopen Increment 11, Increment 12 or the frozen production-readiness queue.

## Increment 13 — Free-Model Inference Resilience — IMPLEMENTED / AUTOMATED VERIFIED / LIVE PENDING

PRs already integrated on `main`:

- **PR #149** — Critical Decision Model Ensemble.
- **PR #150** — Multi-Agent Adversarial Review.
- **PR #151** — Provider Health / Availability, discovery, dynamic reserve pool, governed catalog refresh and multi-provider fail-closed resilience. Optional paid adapters remain outside the canonical free-only runtime.

The canonical CI for PR #151 passed the full unit/integration suite and the E2E suite.

Implemented boundary:

    free-model policy
        -> provider/model health
        -> health-aware ModelRouter
        -> NVIDIA catalog discovery
        -> governed staged candidates
        -> dynamic free reserve pool
        -> existing Director provider failover
        -> critical-decision ensemble
        -> adversarial specialist review

The remaining acceptance step is the dedicated manual LIVE gate on the current canonical main: discover the current official NVIDIA Free Endpoint catalog, prove one real primary inference, then prove a different eligible NVIDIA reserve returns a real response after a clearly labelled controlled primary failure. The controlled reserve drill is implemented by PR #221 but has not yet been executed manually. Gemini is not part of the active NVIDIA-only provider registry; historical Gemini-to-NVIDIA acceptance is retained only as evidence for its older topology. Paid-provider credentials are neither required nor part of the canonical runtime.

Acceptance artifact:

`docs/operations/LIVE_MULTI_PROVIDER_RESILIENCE.md`

**LIVE status:** PENDING execution of the dedicated gate.
