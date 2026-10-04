# Increment 11 — Gate 8 Block 2: Candidate Eligibility Acceptance Plan

**Status:** CLOSED — acceptance verified on canonical `main`  
**Canonical evidence commit:** `de3d6ace8e45bd4f2741a6f9965ce3b4caaea75b`  
**Implementation PRs:** #115 and #116

## Scope

The accepted Block 2 scope was:

- deterministic tests for the eight eligibility contract cases;
- a minimal durable eligibility registry/store;
- a runtime candidate-eligibility check immediately before expired-ownership takeover contention;
- restart/freshness/contradiction behavior and durable diagnostics;
- regression coverage proving eligible candidates still use existing atomic ownership acquisition.

The following remained out of scope and unchanged:

- distributed consensus or cross-machine membership discovery;
- provider/model failover;
- dashboard takeover controls;
- worker lease redesign;
- changes to live authorization, external execution, network enforcement, or mission planning;
- a second election or ownership authority.

## Acceptance matrix

| ID | Scenario | Required result | Observed evidence |
|---|---|---|---|
| G8-EA-1 | B eligible, C explicitly ineligible; same expired mission | Only B may attempt acquisition; C remains unable to mutate | Deterministic acceptance test passes |
| G8-EA-2 | B becomes ineligible after discovery but before acquisition | B is rejected at the authoritative eligibility check; no ownership granted | Deterministic acceptance test passes |
| G8-EA-3 | Candidate record exceeds freshness boundary | Candidate is stale and cannot contend | Deterministic acceptance test passes |
| G8-EA-4 | Eligibility authority unavailable | Fail closed; no candidate acquires | Deterministic acceptance test passes |
| G8-EA-5 | Contradictory records for one stable identity | Fail closed; no guessed winner or ownership mutation | Deterministic acceptance test passes |
| G8-EA-6 | Director process restarts | Stable identity is reused and durable eligibility state remains authoritative | Deterministic restart test passes with explicit stable runtime identity |
| G8-EA-7 | Eligible candidate vs mission ownership | Eligibility alone grants no ownership; successful acquisition still uses existing fencing | Deterministic acceptance test passes |
| G8-EA-8 | Cooperative handoff is REQUESTED/QUIESCING/RELEASED/ACQUIRED | Automatic eligibility cannot bypass handoff protocol | Deterministic acceptance test passes |

## Implementation acceptance

The accepted implementation sequence completed as follows:

1. Runtime/store seams were inspected and the eligibility authority was made injectable.
2. Deterministic acceptance tests were added for all eight cases.
3. A SQLite-backed durable `DirectorEligibilityStore` was implemented with explicit state, capabilities, readiness, timestamps, freshness, generation and fail-closed ambiguity handling.
4. Automatic takeover contention is gated by authoritative eligibility immediately before ownership acquisition, while initial acquisition, active-owner renewal and cooperative handoff retain their existing paths.
5. PR CI passed and canonical post-merge CI passed.
6. This document and the companion contract now record the merged evidence.

## Production wiring

The production factory constructs `DirectorEligibilityStore(store.path)` and injects it into `DirectorRuntime`. The daemon supplies the stable Director worker identity through `AURORA_WORKER_ID` when configured.

The eligibility authority is not used to globally infer a single Director identity: multiple candidates may legitimately coexist. This prevents the unsafe behavior found during implementation where a registry-wide “unique identity” lookup could select the wrong candidate.

## CI evidence

- **PR #115:** merged; required `test` and `e2e-network` checks passed.
- **PR #116:** merged after correcting the eligibility wiring boundary and associated regressions.
- **PR #116 head:** `e83fa5b2c014ed39a7489df6e706134ea7ff440f`.
- **Canonical merge:** `de3d6ace8e45bd4f2741a6f9965ce3b4caaea75b`.
- **Canonical CI #2037:** `success`.
- **Canonical CI #2037 / test:** `success`.
- **Canonical CI #2037 / e2e-network:** `success`.

## Exit criteria

- [x] All eight scenarios pass deterministically.
- [x] Existing Gate 8 Acceptance Block 1 remains green.
- [x] No unrelated subsystem changes.
- [x] Both required CI jobs pass on the PR.
- [x] Canonical post-merge CI passes.
- [x] Gate 8 Block 2 is formally closed.

## Closure decision

**Gate 8 Block 2 — CLOSED.**

The stable Director identity is treated as a runtime/deployment bootstrap invariant and is separate from eligibility authority. The eligibility registry remains authoritative for whether that identity is currently eligible to contend. Mission ownership and fencing remain exclusively authoritative in `DirectorOwnershipStore`.
