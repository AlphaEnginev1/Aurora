# Recovery Drills V1

**Status:** TEST CONTRACT

Block 6 verifies the recovery guarantees already implemented in Aurora. It does not introduce a second recovery subsystem.

## Authoritative boundary

The durable recovery chain is:

`DurableTaskStore leases/claim tokens -> TaskWorker recovery/retry -> DirectorRuntime restart -> durable event/memory state -> production observability`

Acquisition transaction reconciliation is already implemented separately and remains governed by `docs/operations/ACQUISITION.md`.

## Drills

### RD-01 — Worker crash after claim

Simulate a worker disappearing after an atomic task claim and before terminal completion. The lease must expire, a restarted worker must requeue and execute the task, and the stale worker must no longer own the task.

### RD-02 — Stale completion is rejected

After RD-01 requeue/remarking ownership, an old worker using the previous claim token must not be able to commit a terminal state.

### RD-03 — Director runtime restart

A mission partially in flight must be discoverable by a fresh `DirectorRuntime` instance. Restart must continue from durable task state without losing the mission or duplicating a completed terminal result.

### RD-04 — Dependency recovery

A recovered predecessor must allow its dependent task to run; a failed predecessor must block the dependent rather than execute it.

### RD-05 — Durable event cursor recovery

A new runtime/event consumer must resume from the durable event cursor after restart; already acknowledged events must not be replayed as new work.

### RD-06 — Acquisition reconciliation boundary

Existing acquisition recovery/reconciliation must remain covered by its established tests. Block 6 does not reimplement transaction recovery.

## Closure evidence

Block 6 can be marked VERIFIED only when:
- all RD-01..RD-05 automated drills are green on the canonical line;
- the full `test` suite and `e2e-network` suite are green;
- no recovery path changes authoritative mission semantics;
- evidence demonstrates persistence across process/runtime recreation;
- the recovery behavior remains idempotent and claim-token safe.

No new recovery architecture is authorized by this contract.
