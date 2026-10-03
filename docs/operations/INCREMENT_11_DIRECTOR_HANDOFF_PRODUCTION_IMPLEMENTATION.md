# Increment 11 — Cooperative Director Handoff Production Implementation

## Status

**IMPLEMENTATION PHASE OPEN — first implementation block only.**

Gate 5 contract and deterministic E2E acceptance are merged on canonical `main`.
This phase now implements the cooperative handoff path in production.

## Objective

Provide a durable, explicit handoff operation for an active Director:

1. Director A requests handoff to an explicitly designated successor B.
2. The mission enters a durable handoff/quiescing state.
3. A is prevented from starting new Director-controlled mutations.
4. A completes or leaves recoverable any already-running operation.
5. A releases the mission ownership at its current fencing epoch.
6. B acquires the same mission through `DirectorOwnershipStore`, receiving a strictly higher fencing epoch.
7. B reconstructs the existing durable mission state and continues without re-planning.
8. Stale A mutations remain fenced.
9. Handoff state and transitions are durably traceable.

## Implementation boundary

This first production implementation phase is limited to cooperative Director handoff.

### In scope

- durable handoff record/state;
- explicit successor identity;
- atomic request/quiesce/release boundaries;
- integration with the existing `DirectorOwnershipStore`;
- runtime quiescence checks;
- durable handoff events;
- stale predecessor fencing;
- deterministic production tests derived from the merged Gate 5 E2E.

### Explicitly out of scope

- automatic Director election;
- distributed consensus;
- provider/model failover;
- dashboard controls;
- changes to task-worker lease ownership;
- network/proxy/security changes;
- live-authorization changes;
- re-planning or graph replacement during handoff.

## Required invariants

- One mission remains one durable mission identity.
- Successor must be explicitly designated.
- Handoff cannot silently transfer to an unspecified Director.
- Once quiescing begins, no new Director-controlled mutation may be accepted by A.
- Release must be fenced by A's current owner ID and epoch.
- Successor acquisition must advance the fencing epoch.
- A cannot mutate authoritative mission state after release.
- B must consume the existing durable graph/state.
- Completed work must not be repeated.
- Exactly one terminal mission outcome remains possible.

## Implementation order

1. **Durable handoff store/state machine**
2. **DirectorRuntime request/quiesce/release integration**
3. **Successor acquisition/reconstruction integration**
4. **Production E2E and regression suite**
5. **Canonical merge and verification**

No later block is authorized until the preceding block has passed its deterministic tests.

## Current block

**Block 1 — Durable handoff store/state machine.**

The first implementation change must establish the durable handoff record and atomic state transitions without changing DirectorRuntime behavior yet.
