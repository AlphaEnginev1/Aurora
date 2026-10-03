# Increment 11 — Gate 5: Explicit Director Handoff / Release Contract

**Status:** CONTRACT PHASE — NO IMPLEMENTATION
**Canonical base:** `main` at `b7ee462`
**Prerequisites:** Gates 1–4 complete on canonical `main`.
**Scope:** Explicit, operator/runtime-coordinated transfer of mission ownership from the current Director to a designated successor.
**Exclusion:** No automatic election, distributed consensus, provider failover, dashboard controls, or live-authorization changes.

## 1. Objective

Define a safe, explicit handoff mechanism for an in-progress durable mission when the current Director is intentionally replaced while it is still available.

The contract adds a controlled transition:

```
Director A owns mission (epoch E1)
        |
        v
A enters HANDOFF
        |
        +--> stop accepting new Director-controlled work
        +--> finish or safely quiesce in-flight control operations
        +--> persist handoff-ready state
        |
        v
A RELEASES ownership
        |
        v
Director B ACQUIRES mission
        |
        v
B continues the same durable mission (epoch E2)
```

This is distinct from Gate 4's crash/expiry takeover. Gate 4 proves that B can take over after A's lease expires. Gate 5 defines the explicit path for a cooperative transfer while A is still alive.

## 2. Contract invariants

### G5-1 — Same mission identity

The handoff MUST preserve the existing `mission_id`. A handoff MUST NOT create a replacement mission.

### G5-2 — Explicit successor

A handoff MUST identify the intended successor Director/runtime before release. An unspecified successor MUST NOT be treated as an implicit election.

### G5-3 — Quiescence before release

Director A MUST enter a handoff/quiescing state before releasing ownership. After entering this state, A MUST NOT start new Director-controlled mission mutations.

Already-running operations MUST either:
- complete and persist their authoritative result before release; or
- be safely abandoned/recoverable according to existing durable task/lease rules.

### G5-4 — Durable handoff record

The handoff intent and outcome MUST be durably attributable to the mission. At minimum the record MUST identify:
- `mission_id`
- current `owner_id`
- intended successor `owner_id`
- current `fencing_epoch`
- handoff state
- request timestamp
- release timestamp when released
- resulting successor epoch when acquired

Process-local state MUST NOT be authoritative.

### G5-5 — Atomic release boundary

The current Director MUST release ownership through the durable ownership authority. A successful release MUST make the previous ownership epoch unusable for subsequent authoritative mutations.

### G5-6 — Successor acquisition

The designated successor MUST acquire the mission through the existing `DirectorOwnershipStore` fencing mechanism. Its resulting `fencing_epoch` MUST be strictly greater than the released epoch.

### G5-7 — Stale predecessor rejection

After successful release/acquisition, Director A MUST be unable to perform authoritative mission-control mutations under its old epoch.

### G5-8 — No re-planning

Director B MUST reconstruct the existing durable mission/task graph and MUST NOT create a replacement graph solely because the handoff occurred.

### G5-9 — No duplicate work

Tasks already durably succeeded before the handoff MUST NOT execute again solely because of the handoff.

### G5-10 — Single terminal outcome

The mission MUST retain one authoritative terminal outcome. The handoff itself MUST NOT produce a second success/failure outcome.

### G5-11 — Fail closed

If the handoff state is contradictory, the release cannot be durably confirmed, or successor ownership cannot be established safely, the system MUST stop mission-control mutation rather than guessing.

### G5-12 — Traceability

The transition MUST be reconstructable from durable mission/event/ownership identifiers, including predecessor, successor, epochs, and handoff outcome.

## 3. Required handoff state machine

The contract defines these logical states:

```
ACTIVE
  |
  v
HANDOFF_REQUESTED
  |
  v
QUIESCING
  |\
  | +--> HANDOFF_ABORTED -> ACTIVE
  |
  v
RELEASED
  |
  v
SUCCESSOR_ACQUIRED
  |
  v
ACTIVE (successor)
```

Terminal handoff failure MUST NOT silently return to ACTIVE without a durable reason and a valid owner.

A crash during handoff remains governed by Gate 4's expiry/fencing rules; Gate 5 does not weaken crash recovery.

## 4. Forbidden transitions

The implementation MUST reject or fail closed for:
- release by a non-owner;
- release with a stale fencing epoch;
- successor acquisition before the release boundary when the contract requires cooperative transfer;
- old-owner mutation after successful release;
- handoff of one mission into a newly created mission;
- implicit election of an arbitrary successor;
- re-planning solely because of handoff;
- bypassing the existing mission-level fencing mechanism.

## 5. Durable events

A production implementation MUST emit sufficient durable events to reconstruct the transition. Canonical event names proposed by this contract:

- `director_handoff_requested`
- `director_handoff_quiescing`
- `director_handoff_aborted`
- `director_handoff_released`
- `director_handoff_acquired`
- `director_handoff_rejected`

Events MUST identify the mission, predecessor/successor where applicable, fencing epochs, and correlation data. Secrets and provider credentials MUST never appear.

## 6. Acceptance scenario

Before implementation, a deterministic contract E2E MUST prove at minimum:

1. Director A owns an in-progress mission at epoch E1.
2. The mission has completed work plus unfinished dependent work.
3. A requests handoff to explicitly named Director B.
4. A enters quiescence and does not create new Director-controlled mutations.
5. A durably releases ownership.
6. B acquires the same mission at epoch E2, where E2 > E1.
7. A attempts a stale authoritative mutation and is rejected.
8. B reconstructs the existing task graph and does not re-plan it.
9. B executes only unfinished/recoverable work.
10. Previously succeeded work executes exactly once.
11. The mission reaches one terminal outcome.
12. Durable events allow the complete A → release → B transition to be reconstructed.

The test MUST use deterministic clocks and controlled Directors so that handoff semantics are isolated from external provider availability.

## 7. Relationship to Gate 4

Gate 4 remains the safety net for unplanned Director loss:

- Gate 4: lease expiry → fenced takeover.
- Gate 5: explicit cooperative release → designated successor acquisition.

Gate 5 MUST reuse the ownership/fencing authority established in Gates 2–4 rather than creating a second ownership system.

## 8. Explicit non-goals

This contract does NOT authorize:
- automatic Director election;
- distributed consensus;
- automatic provider/model failover;
- dashboard takeover UI;
- multi-machine service discovery;
- changes to live authorization;
- replacement of task-worker leases;
- weakening of proxy/network/security controls.

## 9. Phase boundary

This PR establishes the Gate 5 contract only.

It does NOT implement:
- a new handoff API;
- runtime state-machine code;
- ownership-store changes;
- dashboard controls;
- automatic orchestration.

Implementation may begin only after this contract is merged and its deterministic acceptance test is separately defined and approved.

## 10. Exit criteria

Gate 5 contract phase closes only when:
- this contract is merged to canonical `main`;
- its scope is explicitly accepted;
- the subsequent deterministic E2E acceptance test is defined as the next gated step;
- no unrelated Aurora subsystem is modified.
