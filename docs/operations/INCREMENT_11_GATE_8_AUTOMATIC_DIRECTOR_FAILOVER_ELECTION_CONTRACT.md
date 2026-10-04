# Increment 11 — Gate 8: Automatic Director Failover / Election Contract

**Status:** CONTRACT COMPLETE; ACCEPTANCE BLOCK 1 VERIFIED; CANDIDATE ELIGIBILITY CONTRACT COMPLETE; ACCEPTANCE BLOCK 2 CLOSED; ACCEPTANCE BLOCK 3 OPEN  
**Canonical prerequisite:** Gates 1–7 and the Gate 8 contract are complete on canonical main.  
**Scope:** Automatic recovery of Director control after an active Director lease expires, within one Aurora installation sharing the same authoritative durable mission/ownership state.  
**Exclusions:** No distributed consensus, multi-machine membership discovery, provider/model failover, dashboard takeover controls, live-authorization changes, or worker lease redesign.

## 1. Objective

Define a deterministic protocol by which multiple eligible Director runtimes can recover an active mission after the current Director becomes unavailable and its ownership lease expires.

The protocol must preserve the existing DirectorOwnershipStore as the single mission-level ownership authority.

Target lifecycle:

`ACTIVE(A,E1) -> lease expires -> eligible runtimes contend -> exactly one acquires E2 -> all others remain fenced`

The protocol MUST NOT rely on process-local memory to decide ownership.

## 2. Required invariants

### G8-1 — Single authoritative owner
At most one Director may hold an unexpired mission ownership record at a time.

### G8-2 — Existing fencing authority
Automatic failover MUST use DirectorOwnershipStore acquisition and fencing. Gate 8 MUST NOT create a second ownership authority.

### G8-3 — Expiry-gated takeover
Automatic takeover MUST NOT replace an unexpired owner. The existing lease-expiry rule remains the boundary unless a separately governed cooperative handoff is already in progress.

### G8-4 — Deterministic candidate eligibility
The set of Director runtimes eligible to attempt takeover MUST come from an explicit, durable or otherwise authoritative eligibility source. A runtime MUST NOT self-declare arbitrary authority.

### G8-5 — No split brain
A Director with an expired or fenced epoch MUST be unable to continue authoritative mission-control mutation after another Director acquires a higher epoch.

### G8-6 — Same mission and graph
Failover MUST continue the existing mission and durable task graph. It MUST NOT create a replacement mission or re-plan solely because the previous Director disappeared.

### G8-7 — No duplicate execution
Completed work MUST NOT be re-executed solely because failover occurred. Existing task-level leases remain authoritative for worker execution.

### G8-8 — Deterministic failure behavior
If candidate eligibility, shared ownership state, or durable mission state is contradictory or unavailable, failover MUST stop mission-control mutation rather than guess.

### G8-9 — Traceability
Automatic failover attempts, successful takeover, rejection, fencing, and recovery outcome MUST be attributable to mission identity, Director identity, fencing epoch, and a correlation identifier.

### G8-10 — Live authorization remains independent
Automatic Director failover MUST NOT activate live external execution authorization. The existing explicit human authorization boundary remains authoritative.

## 3. Candidate protocol

The deterministic protocol MUST define:

1. how a Director learns that a mission is eligible for failover;
2. how it validates the current lease is expired;
3. how eligible candidates discover the same mission;
4. how candidates contend through DirectorOwnershipStore;
5. how the successful candidate advances the fencing epoch;
6. how losing candidates stop without mission mutation;
7. how the winner reconstructs durable mission state;
8. how stale predecessors are fenced;
9. how failover is observed and durably traced.

Atomic acquisition remains the final authority in a race.

## 4. Candidate eligibility contract

Before runtime implementation, the acceptance design MUST make explicit:

- Director identity format and uniqueness;
- eligibility state and required capabilities;
- how an eligible Director becomes ineligible;
- whether eligibility is durable across restart;
- how stale candidate records are detected;
- what happens if no eligible candidate exists.

The Candidate Eligibility Authority contract is now complete and its deterministic acceptance is closed. The accepted authority is `DirectorEligibilityStore`, while stable runtime identity remains a runtime/deployment bootstrap invariant.

## 5. Acceptance scenarios

The full deterministic E2E phase covers at least:

1. A owns the mission at epoch E1.
2. A stops renewing and its lease expires.
3. Two eligible candidates B and C observe the same expired mission.
4. Both attempt takeover against DirectorOwnershipStore.
5. Exactly one acquires epoch E2.
6. The loser remains fenced and cannot mutate mission control.
7. The winner reconstructs the existing mission/task graph.
8. Completed work is not repeated.
9. The mission continues to one terminal outcome.
10. Restart of the winner preserves ownership and mission state.
11. A stale predecessor restart remains fenced.
12. Ambiguous or unavailable eligibility causes fail-closed behavior.

## 5A. Acceptance Block 1 evidence

The core expiry-race acceptance is verified on canonical main:
- A owns the mission at fencing epoch 1 and its lease expires;
- eligible B and C contend concurrently;
- exactly one acquires epoch 2 through DirectorOwnershipStore;
- the loser remains fenced;
- stale A remains fenced;
- the winner continues the existing task graph without re-planning or duplicate completed work;
- exactly one terminal outcome remains.

Evidence:
- PR #112 merged as c6f5d261ac39d0549f36d339ab6288ffb4a96913;
- PR runs 1991 and 1992 are green;
- canonical post-merge run 1993 required a rerun of its pre-existing bubblewrap loopback test and rerun attempt 2 completed successfully.

## 5B. Acceptance Block 2 evidence

Candidate Eligibility Authority acceptance is closed on canonical main.

Evidence:
- PR #115 provided the initial authority implementation and its acceptance cases.
- PR #116 corrected production wiring and preserved active-owner/cooperative-handoff boundaries.
- PR #117 formally closed the acceptance documentation.
- Canonical merge after PR #117: `d4b70b7932d50c33dfcbaa255dfd38a18827a091`.
- Canonical CI #2042: `success`, with `test` and `e2e-network` both successful.

The accepted boundary is:
- eligibility filters contenders;
- the runtime/deployment supplies stable Director identity;
- DirectorOwnershipStore alone grants mission ownership and advances fencing;
- cooperative handoff remains authoritative over automatic election;
- eligibility does not activate live authorization.

## 5C. Acceptance Block 3 — Full Automatic Failover E2E

Block 3 now validates the complete Gate 8 lifecycle using the accepted eligibility authority.

The canonical acceptance plan is:
`docs/operations/INCREMENT_11_GATE_8_AUTOMATIC_FAILOVER_ACCEPTANCE_PLAN.md`

Block 3 is limited to deterministic E2E acceptance and the minimum test-fixture/runtime wiring required to exercise the already accepted authorities. It does not authorize a new election algorithm or ownership authority.

## 6. Interaction with cooperative handoff

If a durable cooperative handoff is in REQUESTED, QUIESCING, RELEASED, or ACQUIRED state, automatic election MUST NOT bypass that protocol.

Automatic failover and cooperative handoff remain distinct state transitions and must never silently reinterpret one as the other.

## 7. Explicit non-goals

This contract does NOT authorize:
- distributed consensus protocols;
- multi-machine membership discovery;
- arbitrary self-election;
- provider/model failover;
- task-worker lease changes;
- dashboard takeover UI;
- automatic repair of contradictory durable state;
- live execution authorization changes;
- new mission planning during failover.

## 8. Phase boundary

Block 3 authorizes only deterministic full-path acceptance of the already implemented Gate 8 authorities, plus the narrow test/runtime wiring required to prove that path. All non-goals remain prohibited.

## 9. Exit criteria

Gate 8 remains open until Block 3 passes deterministically, required CI is green on the PR, and canonical post-merge CI is green.
