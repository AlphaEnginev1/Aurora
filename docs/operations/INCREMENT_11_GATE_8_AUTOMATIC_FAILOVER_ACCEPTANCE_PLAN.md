# Increment 11 — Gate 8 Block 3: Automatic Director Failover Acceptance Plan

**Status:** ACTIVE — full deterministic E2E acceptance  
**Prerequisites:** Gate 8 contract, Acceptance Block 1, Candidate Eligibility Authority contract, and Acceptance Block 2 are complete on canonical `main`.  
**Objective:** Prove the complete automatic failover path using the authoritative candidate eligibility source together with the existing `DirectorOwnershipStore`, while preserving the durable mission graph, fencing, restart semantics and single terminal outcome.

## 1. Scope

This block completes the acceptance boundary already defined by the Gate 8 contract. It joins the two previously accepted concerns:

1. candidate eligibility determines whether a Director may contend;
2. `DirectorOwnershipStore` remains the sole authority that grants ownership and advances the fencing epoch.

In scope:

- deterministic end-to-end automatic failover after lease expiry;
- two eligible candidates observing the same expired mission;
- eligibility filtering before ownership contention;
- exactly one atomic winner at the next fencing epoch;
- loser and stale predecessor fencing;
- reconstruction and continuation of the same durable mission/task graph;
- no re-planning and no duplicate completed work;
- winner restart with durable ownership and mission state;
- stale predecessor restart remaining fenced;
- ambiguous/unavailable eligibility fail-closed during the full path;
- durable traceability of takeover, rejection and fencing evidence;
- preservation of exactly one terminal mission outcome.

Out of scope:

- distributed consensus or cross-machine membership discovery;
- provider/model failover;
- dashboard takeover controls;
- worker lease redesign;
- live authorization or external execution changes;
- a second ownership/election authority;
- mission graph replacement or re-planning during failover.

## 2. Acceptance matrix

| ID | Scenario | Required result |
|---|---|---|
| G8-B3-1 | A owns E1; lease expires; B and C are both eligible | Exactly one candidate acquires E2 through `DirectorOwnershipStore` |
| G8-B3-2 | One candidate is explicitly ineligible | Ineligible candidate cannot mutate ownership; eligible candidate may contend |
| G8-B3-3 | Candidate becomes stale or ineligible before contention | Candidate fails closed and cannot acquire |
| G8-B3-4 | Eligibility authority unavailable or contradictory | No candidate acquires; no guessed authority |
| G8-B3-5 | Losing contender attempts mission mutation | Mutation is rejected by ownership/fencing authority |
| G8-B3-6 | Expired predecessor attempts mutation after E2 | Mutation remains fenced |
| G8-B3-7 | Winner continues recovered mission | Same mission/task graph resumes; completed tasks are not repeated |
| G8-B3-8 | Winner restarts while ownership is valid | Same Director identity/ownership state continues without a new fencing epoch |
| G8-B3-9 | Stale predecessor restarts after E2 | Restarted predecessor remains fenced and cannot reclaim mission |
| G8-B3-10 | Mission reaches terminal state after failover | Exactly one authoritative terminal outcome remains |

## 3. Required assertions

Every accepted failover run MUST establish:

- eligibility is consulted before expired-lease takeover;
- a rejected candidate does not successfully call the ownership acquisition path;
- successful acquisition advances the fencing epoch exactly once;
- only one candidate becomes owner;
- losing candidates and stale predecessors cannot perform authoritative mission mutations;
- the winner reuses the existing durable mission and task graph;
- no completed task is re-executed;
- winner restart preserves stable identity and current ownership while the lease remains valid;
- ambiguous/unavailable eligibility is fail-closed;
- takeover and rejection evidence remains attributable to mission ID, Director ID and fencing epoch;
- the mission reaches exactly one terminal outcome.

## 4. Implementation boundary

The minimum implementation/test changes permitted by this block are:

1. inject the existing `DirectorEligibilityStore` into the automatic failover E2E fixtures;
2. register deterministic eligible/ineligible candidates for the scenarios above;
3. add controlled restart/clock cases for winner and stale predecessor;
4. add full-path assertions for eligibility rejection, ownership fencing and terminal-state uniqueness.

No new ownership authority, election algorithm or membership subsystem is permitted.

## 5. Acceptance sequence

1. Director A creates and partially executes a durable mission.
2. A's ownership lease expires.
3. B/C observe the same durable mission and eligibility authority.
4. Eligibility is evaluated immediately before takeover contention.
5. Candidates race through `DirectorOwnershipStore`.
6. Exactly one candidate acquires the next fencing epoch.
7. Losing candidates and A remain fenced.
8. Winner reconstructs durable mission state and completes only unfinished work.
9. Winner restart is exercised while its ownership remains valid.
10. Stale predecessor restart is exercised after the winner holds the higher epoch.
11. The mission reaches one terminal outcome.
12. CI provides canonical evidence.

## 6. Exit criteria

- [ ] G8-B3-1 through G8-B3-10 pass deterministically.
- [ ] Gate 8 Acceptance Block 1 remains green.
- [ ] Block 2 remains closed and unchanged.
- [ ] `DirectorOwnershipStore` remains the sole ownership/fencing authority.
- [ ] No out-of-scope subsystem changes.
- [ ] Required PR `test` and `e2e-network` checks pass.
- [ ] Canonical post-merge CI passes.

**Gate 8 Block 3 is not complete until all criteria above are verified on canonical `main`.**
