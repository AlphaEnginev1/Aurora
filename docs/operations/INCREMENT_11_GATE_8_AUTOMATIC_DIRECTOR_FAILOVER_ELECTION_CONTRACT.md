# Increment 11 — Gate 8: Automatic Director Failover / Election Contract

**Status:** COMPLETE — Contract + Acceptance Block 1 + Candidate Eligibility Authority + Acceptance Block 3 verified on canonical main
**Canonical completion evidence:** af595971199b55d78d52a0769228feefe864ea35
**Canonical CI:** #2057 — success

## 1. Objective

Define a deterministic protocol by which multiple eligible Director runtimes can recover an active mission after the current Director becomes unavailable and its ownership lease expires.

The protocol preserves DirectorOwnershipStore as the single mission-level ownership authority.

Target lifecycle:

ACTIVE(A,E1) -> lease expires -> eligible runtimes contend -> exactly one acquires E2 -> all others remain fenced

The protocol MUST NOT rely on process-local memory to decide ownership.

## 2. Required invariants

### G8-1 — Single authoritative owner
At most one Director may hold an unexpired mission ownership record at a time.

### G8-2 — Existing fencing authority
Automatic failover MUST use DirectorOwnershipStore acquisition and fencing. Gate 8 MUST NOT create a second ownership authority.

### G8-3 — Expiry-gated takeover
Automatic takeover MUST NOT replace an unexpired owner. The existing lease-expiry rule remains the boundary unless a separately governed cooperative handoff is already in progress.

### G8-4 — Deterministic candidate eligibility
The set of Director runtimes eligible to attempt takeover MUST come from an explicit, durable or otherwise authoritative eligibility source.

### G8-5 — No split brain
A Director with an expired or fenced epoch MUST be unable to continue authoritative mission-control mutation after another Director acquires a higher epoch.

### G8-6 — Same mission and graph
Failover MUST continue the existing mission and durable task graph. It MUST NOT create a replacement mission or re-plan solely because the previous Director disappeared.

### G8-7 — No duplicate execution
Completed work MUST NOT be re-executed solely because failover occurred. Existing task-level leases remain authoritative for worker execution.

### G8-8 — Deterministic failure behavior
If candidate eligibility, shared ownership state, or durable mission state is contradictory or unavailable, failover MUST stop mission-control mutation rather than guess.

### G8-9 — Traceability
Automatic failover attempts, successful takeover, rejection, fencing, and recovery outcome are attributable to mission identity, Director identity, fencing epoch, and a deterministic failover correlation identifier where applicable.

### G8-10 — Live authorization remains independent
Automatic Director failover MUST NOT activate live external execution authorization.

## 3. Acceptance scenarios

The accepted Gate 8 coverage includes:

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

## 4. Acceptance evidence

### Acceptance Block 1 — COMPLETE
PR #112 and canonical evidence verified the core expiry-race, epoch advancement, fencing and continuation path.

### Acceptance Block 2 — COMPLETE
PRs #115/#116 implemented and wired the Candidate Eligibility Authority. PR #117 formally closed the acceptance documentation. Canonical CI #2042 passed.

### Acceptance Block 3 — COMPLETE
PR #119 connected the accepted eligibility authority to the complete automatic failover E2E. Canonical merge af595971199b55d78d52a0769228feefe864ea35 passed CI #2057 with both test and e2e-network successful.

Verified in Block 3:

- eligible/ineligible filtering;
- stale candidate rejection;
- unavailable/contradictory authority fail-closed behavior;
- no-record self-promotion rejection;
- exactly one epoch-advancing winner;
- loser and stale predecessor fencing/rejection;
- same durable mission/task graph continuation;
- no duplicate completed work;
- winner restart continuity;
- stale predecessor restart safety;
- deterministic failover correlation evidence;
- exactly one terminal mission outcome.

## 5. Interaction with cooperative handoff

If a durable cooperative handoff is in REQUESTED, QUIESCING, RELEASED, or ACQUIRED state, automatic election MUST NOT bypass that protocol.

Automatic failover and cooperative handoff remain distinct state transitions.

## 6. Explicit non-goals

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

## 7. Completion decision

**Gate 8 — COMPLETE.**

All required Gate 8 acceptance blocks have passed their deterministic acceptance boundaries and canonical verification. DirectorOwnershipStore remains the sole mission ownership/fencing authority. No subsequent Gate 8 block is defined by the current Increment 11 roadmap.
