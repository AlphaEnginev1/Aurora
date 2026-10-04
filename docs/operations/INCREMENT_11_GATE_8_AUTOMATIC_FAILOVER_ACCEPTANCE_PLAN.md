# Increment 11 — Gate 8 Block 3: Automatic Director Failover Acceptance Plan

**Status:** CLOSED — full deterministic E2E acceptance verified on canonical main
**Canonical evidence commit:** af595971199b55d78d52a0769228feefe864ea35
**Acceptance PR:** #119
**Canonical CI:** #2057 — success

## 1. Scope

This block completed the acceptance boundary already defined by the Gate 8 contract. It joined the two previously accepted concerns:

1. candidate eligibility determines whether a Director may contend;
2. DirectorOwnershipStore remains the sole authority that grants ownership and advances the fencing epoch.

Accepted scope:

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
- durable traceability of takeover, rejection, fencing and terminal outcome;
- preservation of exactly one terminal mission outcome.

Out of scope and unchanged:

- distributed consensus or cross-machine membership discovery;
- provider/model failover;
- dashboard takeover controls;
- worker lease redesign;
- live authorization or external execution changes;
- a second ownership/election authority;
- mission graph replacement or re-planning during failover.

## 2. Acceptance matrix

| ID | Scenario | Required result | Evidence |
|---|---|---|---|
| G8-B3-1 | A owns E1; lease expires; B and C are both eligible | Exactly one candidate acquires E2 through DirectorOwnershipStore | PASS — PR #119 / CI #2056 |
| G8-B3-2 | One candidate is explicitly ineligible | Ineligible candidate cannot mutate ownership; eligible candidate may contend | PASS — PR #119 / CI #2056 |
| G8-B3-3 | Candidate becomes stale or ineligible before contention | Candidate fails closed and cannot acquire | PASS — PR #119 / CI #2056 |
| G8-B3-4 | Eligibility authority unavailable or contradictory | No candidate acquires; no guessed authority | PASS — PR #119 / CI #2056 |
| G8-B3-5 | Losing contender attempts mission mutation | Mutation is rejected by ownership/fencing authority | PASS — PR #119 / CI #2056 |
| G8-B3-6 | Expired predecessor attempts mutation after E2 | Mutation remains fenced | PASS — PR #119 / CI #2056 |
| G8-B3-7 | Winner continues recovered mission | Same mission/task graph resumes; completed tasks are not repeated | PASS — PR #119 / CI #2056 |
| G8-B3-8 | Winner restarts while ownership is valid | Same Director identity/ownership state continues without a new fencing epoch | PASS — PR #119 / CI #2056 |
| G8-B3-9 | Stale predecessor restarts after E2 | Restarted predecessor remains fenced and cannot reclaim mission | PASS — PR #119 / CI #2056 |
| G8-B3-10 | Mission reaches terminal state after failover | Exactly one authoritative terminal outcome remains | PASS — PR #119 / CI #2056 |

## 3. Required assertions

The accepted failover suite establishes:

- eligibility is consulted before expired-lease takeover;
- rejected candidates do not successfully acquire ownership;
- successful acquisition advances the fencing epoch exactly once;
- only one candidate becomes owner;
- losing candidates and stale predecessors cannot perform authoritative mission mutations;
- the winner reuses the existing durable mission and task graph;
- no completed task is re-executed;
- winner restart preserves stable identity and current ownership while the lease remains valid;
- ambiguous/unavailable eligibility is fail-closed;
- takeover/rejection/fencing/terminal evidence carries deterministic failover correlation where applicable;
- the mission reaches exactly one terminal outcome.

## 4. Implementation boundary

The accepted Block 3 implementation contains only:

1. the existing DirectorEligibilityStore injected into automatic failover E2E fixtures;
2. deterministic eligible/ineligible candidate registration;
3. controlled restart/clock cases;
4. deterministic failover correlation propagation on takeover/rejection/fencing/terminal events;
5. full-path assertions for eligibility, ownership fencing and terminal-state uniqueness.

No new ownership authority, election algorithm or membership subsystem was introduced.

## 5. Acceptance sequence

1. Director A creates and partially executes a durable mission.
2. A's ownership lease expires.
3. B/C observe the same durable mission and eligibility authority.
4. Eligibility is evaluated immediately before takeover contention.
5. Candidates race through DirectorOwnershipStore.
6. Exactly one candidate acquires the next fencing epoch.
7. Losing candidates and A remain fenced.
8. Winner reconstructs durable mission state and completes only unfinished work.
9. Winner restart is exercised while its ownership remains valid.
10. Stale predecessor restart is exercised after the winner holds the higher epoch.
11. The mission reaches one terminal outcome.
12. CI provides canonical evidence.

## 6. Closure evidence

- PR #119: merged to canonical main.
- PR #119 head: 239c54a0dd06c09bb3a075536a1eaf1a6d057df1.
- Canonical merge commit: af595971199b55d78d52a0769228feefe864ea35.
- PR CI #2056: success; test and e2e-network both succeeded.
- Canonical CI #2057: success; test and e2e-network both succeeded.
- During acceptance, one E2E assertion was corrected to distinguish a restarted stale predecessor's normal ownership rejection from a predecessor holding a stale local fence; the final PR and canonical CI passed.
- No changes were made to DirectorOwnershipStore ownership/fencing semantics.

## 7. Exit criteria

- [x] G8-B3-1 through G8-B3-10 pass deterministically.
- [x] Gate 8 Acceptance Block 1 remains green.
- [x] Block 2 remains closed and unchanged.
- [x] DirectorOwnershipStore remains the sole ownership/fencing authority.
- [x] No out-of-scope subsystem changes.
- [x] Required PR test and e2e-network checks pass.
- [x] Canonical post-merge CI passes.

**Gate 8 Block 3 — CLOSED.**
