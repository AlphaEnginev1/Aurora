# Recovery Drills V1

**Status:** VERIFIED

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

### RD-07 — Director decision receipt replay

Simulate process termination after the durable `director_review` receipt is committed but before task enqueue or terminal publication. On restart, Aurora must replay the exact stored decision and phase-qualified graph without calling the planner for a replacement decision. A continuation must enqueue one copy of each follow-up task and commit `director_decision_executed`, `mission_reopened`, and the recovery receipt atomically with those rows. A terminal decision must create its missing execution/terminal markers once. Malformed receipts or mismatched task rows must block fail-closed.

Also simulate termination after `director_review_started` but before `director_review` is written. The restarted Director must reuse that round number instead of consuming a review round that never produced a decision.

### RD-08 — Transient follow-up enqueue failure after a durable decision

Inject a one-time storage error after a valid `continue` decision receipt has committed but before follow-up task rows can be enqueued. The runtime must not write a terminal `mission_blocked` marker. Its next cycle must replay the exact saved graph from the receipt, without asking the planner for the same decision again, and create exactly one follow-up task set plus the missing decision/reopen/recovery markers. A later successful phase must still reach its own Director review and terminal decision.

### RD-09 — Project and correlation identity across inference and Workbench execution

Run a project-bound task and a project-bound conversation whose model-generated Workbench options deliberately name a different project and correlation ID. Runtime-owned scope must override those options. The inference request, Workbench request, task result receipt, mission correlation, and terminal review must remain bound to the canonical identity. Mutating a versioned durable result receipt to name another correlation or project must cause the Director's completion evidence gate to reject success.

## Closure evidence

Block 6 is VERIFIED on the canonical `main` line.

- CI 1396: Recovery Drills branch — `test` PASS, `e2e-network` PASS.
- CI 1398: canonical `main` after merge — `test` PASS, `e2e-network` PASS.
- RD-01..RD-05 are green.
- RD-07 is green in PR #278 CI run 38918770649: 832 unit tests passed; 57 E2E passed and 4 skipped.
- RD-07 verifies replay of a saved continuation, replay of a terminal decision, and reuse of an uncompleted review round after restart.
- Existing acquisition transaction reconciliation remains covered by its established recovery tests.
- No new recovery subsystem was introduced; the drills verify the already-integrated durable recovery mechanisms.
- Recovery remains idempotent and claim-token safe, and restart evidence is preserved in the durable task/event state.

No new recovery architecture is authorized by this contract.
