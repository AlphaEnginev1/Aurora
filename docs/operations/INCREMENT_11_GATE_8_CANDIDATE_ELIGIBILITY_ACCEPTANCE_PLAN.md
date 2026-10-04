# Increment 11 — Gate 8 Block 2: Candidate Eligibility Acceptance Plan

**Status:** ACTIVE — deterministic acceptance and minimal required integration only  
**Prerequisites:** Gate 8 contract, Acceptance Block 1, and Candidate Eligibility Authority contract are merged on canonical `main`.  
**Objective:** Prove that only authoritative, current, eligible Director identities may contend for an expired mission, while DirectorOwnershipStore remains the sole owner/fencing authority.

## Scope

In scope:
- deterministic tests for the eight eligibility contract cases;
- a minimal durable eligibility registry/store if required to satisfy those tests;
- a runtime candidate-eligibility check immediately before takeover contention;
- restart/freshness/contradiction behavior and durable diagnostics;
- regression coverage proving eligible candidates still use existing atomic ownership acquisition.

Out of scope:
- distributed consensus or cross-machine membership discovery;
- provider/model failover;
- dashboard takeover controls;
- worker lease redesign;
- changes to live authorization, external execution, network enforcement, or mission planning;
- a second election or ownership authority.

## Acceptance matrix

| ID | Scenario | Required result |
|---|---|---|
| G8-EA-1 | B eligible, C explicitly ineligible; same expired mission | Only B may attempt acquisition; C remains unable to mutate |
| G8-EA-2 | B becomes ineligible after discovery but before acquisition | B is rejected at the authoritative eligibility check; no ownership granted |
| G8-EA-3 | Candidate record exceeds freshness boundary | Candidate is stale and cannot contend |
| G8-EA-4 | Eligibility authority unavailable | Fail closed; no candidate acquires |
| G8-EA-5 | Contradictory records for one stable identity | Fail closed; no guessed winner or ownership mutation |
| G8-EA-6 | Director process restarts | Stable identity and eligibility state are reconstructed; no arbitrary new identity |
| G8-EA-7 | Eligible candidate vs mission ownership | Eligibility alone grants no ownership; successful acquisition still advances existing fencing epoch |
| G8-EA-8 | Cooperative handoff is REQUESTED/QUIESCING/RELEASED/ACQUIRED | Automatic eligibility cannot bypass handoff protocol |

## Required test assertions

- Rejected candidates do not call a successful ownership acquisition path.
- A rejected candidate cannot publish authoritative mission mutations.
- Successful takeover still advances the existing fencing epoch exactly once.
- The previous owner and losing contenders remain fenced.
- The same durable mission/task graph is continued; no re-planning or duplicate completed work.
- Eligibility errors are attributable and fail closed without leaking credentials.
- The existing single terminal-outcome invariant remains intact.

## Implementation sequence

1. Inspect current runtime/store seams and select the narrowest injectable eligibility authority.
2. Add deterministic tests for all eight cases, including controlled clock and restart.
3. Implement the minimal durable record/store semantics required by those tests.
4. Gate candidate contention on authoritative eligibility immediately before ownership acquisition; do not duplicate ownership logic.
5. Run focused tests, full `test` and `e2e-network` CI, then verify canonical post-merge CI.
6. Update roadmap/status only from observed merged evidence.

## Exit criteria

- All eight scenarios pass deterministically.
- Existing Gate 8 Acceptance Block 1 remains green.
- No unrelated subsystem changes.
- Both required CI jobs pass on the PR and canonical `main`.
- Gate 8 is not declared complete until these criteria are verified.
