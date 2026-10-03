# Increment 11 — Director Ownership & Fenced Takeover Contract

**Status:** OPERATIONAL CONTRACT + ACCEPTANCE SCENARIO  
**Canonical base:** `main` at `76dd1f4`  
**Prerequisite:** Director Handoff / Failover contract gate is merged and its E2E test passes.

## 1. Purpose

The first Increment 11 gate proved that a replacement Director can continue an existing durable mission. This phase defines the missing operational safety mechanism that decides **which Director is currently authorized to act for a mission**.

The mechanism MUST prevent two Director runtimes from concurrently controlling the same mission and MUST prevent a stale Director from committing work after a takeover.

Target lifecycle:

```
Director A acquires mission lease
        |
        v
A owns mission with fencing epoch E1
        |
        +--> A heartbeats / renews
        |
A becomes unavailable
        |
        v
lease expires
        |
        v
Director B atomically acquires lease
        |
        v
B receives fencing epoch E2 > E1
        |
        +--> B continues the same mission
        |
stale A attempts mutation
        |
        v
REJECTED by fencing
```

## 2. Ownership record

A mission ownership record MUST be durable and keyed by `mission_id`.

Minimum fields:
- `mission_id`
- `owner_id` — unique runtime/Director instance identity
- `fencing_epoch` — monotonically increasing integer
- `lease_until` — absolute UTC expiry
- `acquired_at`
- `updated_at`

The ownership record is authoritative for Director control. Process-local memory MUST NOT be authoritative.

## 3. Lease rules

### O1 — Single owner

At most one unexpired ownership record exists for a mission.

### O2 — Atomic acquisition

Initial acquisition and takeover MUST be atomic. Two Directors racing to acquire an unowned/expired mission MUST result in exactly one winner.

### O3 — Renewal requires current ownership

A Director may renew only when both `owner_id` and `fencing_epoch` match the durable ownership record and the lease has not already expired.

### O4 — Takeover requires expiry

A takeover MUST NOT replace an unexpired owner unless an explicit future handoff/release mechanism is introduced and separately governed.

### O5 — Monotonic fencing

Every successful takeover increments `fencing_epoch`. Epoch values MUST never move backwards.

### O6 — Stale-owner rejection

Any operation that can mutate authoritative mission control state MUST be fenced by the current ownership epoch. A stale Director with an older epoch MUST receive a deterministic rejection and MUST NOT alter mission state.

### O7 — Fail closed on ambiguity

If ownership state cannot be read or validated, the Director MUST stop mission control rather than assume it is owner.

## 4. Takeover rules

When Director B attempts takeover:

1. B identifies the existing mission by `mission_id`.
2. B reads the durable ownership record.
3. If the existing lease is unexpired, B is denied.
4. If the lease is expired, B atomically replaces the owner and increments the epoch.
5. B reconstructs mission state using the existing Director Handoff contract.
6. B may continue runnable work under the new epoch.
7. B's successful control actions are attributable to B and the new epoch.

The takeover mechanism MUST NOT create a new mission or new task graph.

## 5. Fencing boundary

The fencing token MUST protect every Director-controlled authoritative transition, including at minimum:
- mission-level terminalization;
- Director review/decision persistence;
- Director-driven replanning that mutates the durable task graph;
- any future Director-specific mission-control mutation.

Worker task claims remain protected by the existing task-level claim token/lease system; this phase adds a **mission-level Director ownership fence** rather than replacing worker leases.

## 6. Required events

A successful operational implementation MUST emit durable events sufficient to reconstruct:

- `director_ownership_acquired`
- `director_ownership_renewed`
- `director_ownership_takeover`
- `director_ownership_rejected`
- `director_ownership_fence_rejected`

Each event SHOULD include:
- `mission_id`
- `owner_id`
- `fencing_epoch`
- previous owner/epoch when relevant;
- lease expiry when relevant;
- correlation identifier.

No provider secret or credential may appear in ownership events.

## 7. Acceptance scenario before production implementation

This phase's deterministic contract test intentionally uses a **test-only ownership authority**. It is an executable specification of the state machine, not evidence that production code already implements it.

The test MUST prove:

1. Director A acquires an unowned mission.
2. Director B is rejected while A's lease is valid.
3. A's renewal extends the same epoch.
4. A is considered unavailable and stops renewing.
5. After expiry, B atomically acquires the mission.
6. B receives a strictly higher fencing epoch.
7. A's stale mutation is rejected.
8. B's mutation succeeds with the current epoch.
9. Only one current owner exists.
10. The event history contains the acquisition, rejected takeover, renewal, takeover, and stale-fence rejection.

## 8. Explicit non-goals

This phase does NOT implement:
- automatic election across the whole Aurora installation;
- network/service discovery;
- distributed consensus;
- provider failover;
- dashboard controls;
- multi-machine orchestration;
- automatic live authorization.

Those are separate decisions and must not be smuggled into the ownership implementation.

## 9. Implementation gate

Only after this contract and its deterministic acceptance scenario are merged and green may production implementation begin.

The production implementation MUST preserve O1–O7 and the event/fencing boundaries above.
