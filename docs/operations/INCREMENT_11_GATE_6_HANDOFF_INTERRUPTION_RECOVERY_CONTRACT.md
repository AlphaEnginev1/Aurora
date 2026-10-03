# Increment 11 — Gate 6: Handoff Interruption / Recovery Contract

**Status:** CONTRACT PHASE — NO IMPLEMENTATION  
**Canonical base:** `main` at `e7d14c1`  
**Prerequisites:** Gates 1–5 and cooperative handoff production implementation complete on canonical `main`.  
**Scope:** Deterministic recovery semantics when a Director process is interrupted or restarted during an explicit cooperative handoff.  
**Exclusion:** No automatic election, distributed consensus, provider/model failover, dashboard controls, live-authorization changes, or replacement of the existing ownership/fencing authority.

## 1. Objective

Prove that a cooperative Director handoff remains safe when either the predecessor or successor process is interrupted at a handoff boundary.

The recovery contract is:

```
ACTIVE(A, E1)
   |
   +--> REQUESTED / QUIESCING
   |       |
   |       +--> interruption/restart --> durable state reconstructable
   |
   +--> RELEASED(E1)
           |
           +--> interruption/restart --> B can still acquire E2 > E1
                                      |
                                      +--> interruption/restart --> mission remains durable
```

The durable mission/task/event/ownership state remains authoritative across process restart.

## 2. Required invariants

### G6-1 — Durable handoff state survives restart

Restarting a Director process MUST NOT erase or reset the persisted handoff record, mission identity, task graph, ownership epoch, or transition history.

### G6-2 — No ambiguous ownership after interruption

An interrupted predecessor MUST NOT regain authoritative ownership merely because its process restarts. Existing fencing and lease rules remain authoritative.

### G6-3 — Released handoff remains released

Once ownership is durably released, a predecessor restart MUST NOT reopen or reactivate the same handoff implicitly.

### G6-4 — Designated successor remains explicit

A successor restart MUST reconstruct the designated successor identity from durable state. An arbitrary Director MUST NOT be selected.

### G6-5 — Epoch monotonicity

A successor acquiring after a released or expired predecessor MUST obtain a fencing epoch strictly greater than the predecessor epoch.

### G6-6 — No duplicate execution

Tasks durably succeeded before interruption MUST NOT execute again solely because of restart or handoff recovery.

### G6-7 — No re-planning

A recovered successor MUST reconstruct the existing durable graph and MUST NOT create a replacement graph solely because a process restarted during handoff.

### G6-8 — Single terminal outcome

Interruption and recovery MUST NOT create duplicate terminal mission outcomes.

### G6-9 — Fail closed on contradiction

If durable handoff state, ownership state and mission state contradict one another, recovery MUST stop mission-control mutation rather than guessing.

### G6-10 — Traceability

Restart/recovery transitions MUST remain reconstructable from durable mission, handoff, ownership, task and event identifiers.

## 3. Required recovery cases

The deterministic acceptance test MUST cover, at minimum:

1. A owns the mission at epoch E1.
2. A requests handoff and reaches `REQUESTED`.
3. A runtime instance is restarted from durable state.
4. The handoff remains attributable to A → B and cannot be silently reassigned.
5. A reaches `QUIESCING` and is restarted before release.
6. No new Director-controlled mutation is accepted by the predecessor after quiescence.
7. A releases ownership durably.
8. A is restarted after `RELEASED` and remains unable to mutate authoritatively.
9. B acquires the same mission at epoch E2 > E1.
10. B is restarted after acquisition and reconstructs the same mission/task graph.
11. Completed tasks are not repeated.
12. Only unfinished/recoverable tasks execute.
13. The mission reaches one terminal outcome.
14. Durable records reconstruct the entire interruption/recovery path.

## 4. Acceptance method

The test MUST use:
- deterministic clocks;
- controlled Director implementations;
- the existing `DirectorOwnershipStore`;
- the existing `DirectorHandoffStore`;
- the existing durable task/event state.

The test MUST not depend on external model/provider availability.

The acceptance test MUST distinguish:
- persisted ownership history vs active ownership;
- handoff state vs process-local runtime state;
- lease expiry takeover vs explicit cooperative successor acquisition.

## 5. Reuse of existing safety boundaries

Gate 6 MUST NOT create a second ownership or handoff authority.

Existing guarantees remain:
- Gate 4 provides expiry/fencing recovery when a Director disappears unexpectedly.
- Gate 5 provides explicit cooperative release to a designated successor.
- Gate 6 proves that process interruption around those boundaries preserves the same durable safety properties.

## 6. Explicit non-goals

This contract does NOT authorize:
- automatic Director election;
- distributed consensus;
- automatic provider/model failover;
- dashboard takeover UI;
- multi-machine service discovery;
- live execution authorization changes;
- task-worker lease redesign;
- network/proxy/security changes;
- new mission graph generation during recovery.

## 7. Phase boundary

This phase is contract-only.

It authorizes:
- this recovery contract;
- a deterministic acceptance test definition;
- documentation of observed recovery gaps.

It does NOT authorize runtime code changes until the contract and the subsequent acceptance-test gate are both closed.

## 8. Exit criteria

Gate 6 contract phase closes only when:
- this contract is merged to canonical `main`;
- its acceptance cases are explicit and reviewable;
- the subsequent deterministic E2E acceptance test is defined as the next gated step;
- no unrelated Aurora subsystem is modified.
