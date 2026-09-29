# Aurora Consolidation Matrix

**Audit status: ACTIVE — FEATURE FREEZE**

This document is the working inventory for the Aurora consolidation gate. It is intentionally
evidence-oriented: implementation existence is separated from runtime integration and from live-provider
verification.

## Canonical baseline

- Audit branch: `runtime/consolidation-gate`
- Source implementation baseline: `runtime/increment-10-director-contract`
- Baseline commit: `64193c0cbbaccc0920c87f20054c83bc0c482d556`
- CI 1287: SUCCESS
- CI 1288: SUCCESS
- CI 1289: SUCCESS
- Consolidation issue: #50

## 1. System inventory

| Area | Current state | Evidence / notes |
|---|---|---|
| Repository / governance | IMPLEMENTED | Architecture, governance, security, CI and operational docs exist. |
| Capability Broker / Registry | IMPLEMENTED | Registered capabilities are policy checked before execution. |
| Autonomous capability discovery | IMPLEMENTED | PR #45 merged; current runtime has capability resolution/discovery services. |
| Acquisition | IMPLEMENTED | APT/PyPI acquisition, verification, transactions, staging, recovery and dependency closure are present. |
| Sandbox / workspace isolation | IMPLEMENTED | Bubblewrap-based isolated execution and deterministic per-attempt workspaces are present. |
| Network controls | IMPLEMENTED / HARDENING REMAINS | Gateway, proxy and network dataplane exist. OS-level transparent enforcement is still documented as a separate hardening limitation. |
| Inference Fabric / ModelRouter | IMPLEMENTED | Provider-neutral routing, model health/capability filtering, scoring and learned-performance inputs exist. |
| NVIDIA provider | IMPLEMENTED | NVIDIA API adapter exists; live gate is opt-in and therefore not equivalent to live production verification. |
| Director provider abstraction | IMPLEMENTED | Vendor-neutral Director provider contract plus Gemini adapter exist. |
| Director 24/7 runtime | IMPLEMENTED | Durable Event Bus, worker leases, recovery, adaptive reviews and daemon are integrated. |
| Loop safety | BOUNDED PER MISSION | `max_review_rounds=3` by default; `run_until_terminal` has `max_cycles=1000`; `run_forever` is intentionally continuous for the 24/7 service. |
| Organization / departments | IMPLEMENTED | Organization Registry, role/capability authorization and model preferences exist. |
| Specialist skills foundation | IMPLEMENTED | Versioned SkillRegistry, role defaults, task assignments, capability checks and persistence exist. |
| Skill evaluation / learning | IMPLEMENTED | Durable evaluations and trusted performance history exist. |
| Skill evolution | IMPLEMENTED | Evidence-backed proposals, validation, approval, promotion and rollback are implemented. |
| Specialist acquisition | IMPLEMENTED | Governed discovery, construction and explicit promotion exist. |
| Computer operator / credentials | IMPLEMENTED | Governed computer operation and credential/identity broker PRs are merged. |
| Research domain | IMPLEMENTED | Research requests, sources, evidence, provenance and fail-closed states exist. |
| Research parsing / claims | IMPLEMENTED | Deterministic visible-text parsing, claim extraction and provenance binding exist. |
| Research verification | IMPLEMENTED | Independent-source verification and contradiction handling exist. |
| Trusted knowledge / citation graph | IMPLEMENTED | Verified-only knowledge artifacts and durable citation edges exist. |
| Research delegation | IMPLEMENTED | Durable `research` task path and bounded result summaries exist. |
| Research → learning / routing | IMPLEMENTED | Verified-artifact trust gate and later adaptive routing proof exist; current proof provider is controlled/test-only. |
| Research discovery provider | PARTIAL INTEGRATION | `HttpSearchProvider` exists, but the current `factory.py` constructs `ResearchService` without wiring a discovery provider. Do not count this as a complete autonomous research-discovery runtime path yet. |
| Director decision learning | IMPLEMENTED | Durable evaluation, evidence sufficiency and performance aggregation exist. |
| Comparative decision evidence | IMPLEMENTED | Explicit alternatives, independent comparisons, aggregation and cross-decision replication are present. |
| Empirical decision knowledge | IMPLEMENTED | Repeated comparable observations can be promoted as observational knowledge; no causality is asserted. |
| Director policy evolution | IMPLEMENTED | Governed proposal, validation, canary, rollback and active registry exist. |
| Shadow canary | IMPLEMENTED | Candidate decision is simulated without candidate execution; evaluation is automatic and rollback is fail-closed on integrity violation. |
| Active typed policy contract | IMPLEMENTED | Immutable typed contract and durable store exist. |
| Live provider verification | NOT YET VERIFIED | Dedicated live Gemini and NVIDIA workflows are manual/opt-in. Current audit evidence does not show a successful live-provider run. |

## 2. Major milestones already completed

The following are already represented in the integration line and must NOT be reopened as new feature increments:

- Phase 8 Adaptive Agent Intelligence — merged at `d5621d08e0207eaa222620f3d6fc7f7f016b0edf`.
- Parallel runtime hardening — PR #26 merged.
- Mission team runtime and adaptive team behavior — PRs #27 and #28 merged.
- Skill acquisition / construction / promotion / evolution / rollback — PRs #29–#38 merged.
- Model performance learning / routing integration — PRs #39–#42 and #49 merged.
- Governed web discovery provider — PR #43 merged.
- Tool artifact injection — PR #44 merged.
- Autonomous capability discovery / persistent acquisition — PR #45 merged.
- Governed computer operator / credential boundary — PRs #46–#47 merged.
- Phase 9 research increments through adaptive routing proof are represented in the current line.
- Director decision learning, comparative evidence, knowledge promotion, policy lifecycle, shadow canary and active contract work are represented by CIs 1193–1289.

## 3. GitHub process hazards found

### Historical stacked branches

The repository contains many historical `runtime/*` branches whose implementations were later
integrated into the current line. They remain visible and should be treated as historical work,
not as an independent future backlog.

### Historical PR cleanup

The obsolete stacked PR backlog has now been closed for consolidation: PRs #1–#23, #25 and #48.
PRs #24 and #26–#47 were already merged. There are currently **no open pull requests** in the repository.
This removes the primary GitHub-level source of false "pending work" signals.

### Main branch divergence

At audit time, `main` points to `232522026917160e7faa032c72ff92a8e3ae4060`, while the implementation
baseline is `64193c0cbbaccc0920c87f20054c83bc0c482d556`. The comparison reports the implementation branch
as 810 commits ahead and 6 commits behind `main`. Until this is reconciled, `main` cannot be treated
as the authoritative Aurora implementation line.

### Documentation drift

The current ROADMAP and several operational documents still describe already-completed work as future
"Next increments". README still describes the repository as "Foundation bootstrap in progress". These
statements are not reliable project-state indicators.

## 4. Duplicate implementation finding

No duplicate primary implementation of the following was found in the current source tree:

- DirectorRuntime
- ModelRouter
- SkillLearningStore
- ResearchService
- Director decision-learning store
- Director policy lifecycle / canary / active contract stores

The `aurora_director` package is an integration wrapper, not a second Director implementation.

The repeated initial commit message `feat: establish Aurora autonomous architecture v1` appears 19 times,
but inspection shows these are individual initial-file commits (architecture / ADR material), not 19
copies of the runtime implementation.

## 5. Director loop audit

The continuous service loop is intentional:

`run_forever() -> run_once() -> worker execution -> terminal review/replan`.

Mission-level safeguards are present:

- default `max_review_rounds=3`;
- exhausted review budget produces `mission_blocked`;
- `run_until_terminal` has `max_cycles=1000`;
- durable event/task state is authoritative across restart;
- later Director reviews cannot satisfy earlier decision evidence outside their bounded interval.

Therefore the current code does not show an uncontrolled per-mission review/replan loop.

## 6. Work classification

### CLOSED / DO NOT REOPEN

Core autonomy, acquisition, sandbox, network gateway/proxy, 24/7 runtime, organization,
specialist skills, skill learning/evolution lifecycle, model routing evidence, research evidence
pipeline, capability discovery, computer/credential governance, Director decision learning,
comparative evidence, knowledge promotion, policy evolution, canary and active contract.

### PARTIALLY INTEGRATED

- Research discovery provider exists but is not wired into the ResearchService construction in the
  current runtime factory.
- OS-level transparent network enforcement remains a separate hardening boundary.
- Some "live" provider paths are opt-in/manual and have not been established as continuously verified
  runtime providers.

### CONSOLIDATION REQUIRED BEFORE NEW FEATURES

- Choose the canonical implementation branch.
- Reconcile `main` with the implementation line.
- Close/archive obsolete stacked PRs or explicitly mark them historical.
- Replace stale roadmap/documentation statements.
- Keep this matrix as the authoritative status record until the canonical roadmap is repaired.
- Produce a finite, evidence-backed remaining-work list.

## 7. Freeze rule

No new Aurora functionality is to be implemented while Issue #50 is open.

Allowed changes are limited to consolidation documentation, verification, tests, and fixes required
to make already-implemented behavior accurately represented and safely verifiable.

## 8. Audit conclusion

The principal risk is not a second copy of Aurora's runtime logic. The principal risk is repository
state drift: historical branches and open stacked PRs plus stale documentation make completed features
look unfinished and can cause future work to be reimplemented.

This gate therefore becomes the control point for future planning. A new implementation increment is
not valid until it appears in the reconciled remaining-work list after this gate is closed.
