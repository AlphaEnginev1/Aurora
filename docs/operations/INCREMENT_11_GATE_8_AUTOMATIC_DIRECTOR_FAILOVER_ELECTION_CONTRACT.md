# Increment 11 — Gate 8: Automatic Director Failover / Election Contract

**Status:** CONTRACT PHASE — NO IMPLEMENTATION
**Canonical prerequisite:** Gates 1–7 are complete on canonical main.
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

Gate 8 does not prescribe a specific membership implementation. The authoritative eligibility source must be selected and separately validated before production activation.

## 5. Acceptance scenarios

The subsequent deterministic E2E phase MUST cover at least:

1. A owns mission at epoch E1.
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

This phase authorizes only:
- the Gate 8 contract;
- deterministic acceptance-test design;
- documentation of implementation gaps.

No runtime implementation changes are authorized until this contract is merged and the subsequent acceptance-test gate is separately opened.

## 9. Exit criteria

The Gate 8 contract phase closes only when:

- this contract is merged to canonical main;
- candidate eligibility and race semantics are explicit and reviewable;
- the subsequent deterministic E2E acceptance phase is separately scoped;
- no unrelated Aurora subsystem is modified.
