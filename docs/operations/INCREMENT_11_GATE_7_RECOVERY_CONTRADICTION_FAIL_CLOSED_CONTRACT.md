# Increment 11 — Gate 7: Recovery Contradiction / Fail-Closed Contract

**Status:** CONTRACT PHASE — NO IMPLEMENTATION
**Canonical prerequisite:** Gate 6 handoff interruption/recovery acceptance is complete on canonical main.
**Scope:** Deterministic handling of contradictory durable Director handoff, ownership/fencing, and mission/task-control state during recovery.
**Exclusions:** No automatic Director election, distributed consensus, provider/model failover, dashboard takeover controls, live-authorization changes, worker lease redesign, or network/proxy/security changes.

## 1. Objective

Aurora must not guess when the durable records required to resume mission control cannot be reconciled safely.

Gate 7 defines the fail-closed boundary for contradictions discovered while a Director runtime reconstructs or resumes a mission.

If durable handoff state, durable Director ownership, and durable mission/task state disagree in a way that could permit an incorrect Director mutation, recovery MUST stop mission-control mutation and preserve the evidence needed to diagnose the contradiction.

## 2. Required invariants

### G7-1 — No mutation on contradiction

When required recovery authorities contradict one another, the Director MUST NOT persist a Director-controlled mutation for the affected mission.

### G7-2 — No guessed repair

Recovery MUST NOT infer which durable authority is correct, silently rewrite the conflicting records, or reopen/close a handoff merely to make state appear consistent.

### G7-3 — Existing fencing remains authoritative

A contradiction MUST NOT weaken or bypass the existing DirectorOwnershipStore fencing boundary. A stale or ambiguous Director remains unable to act authoritatively.

### G7-4 — Explicit safe-stop outcome

The runtime MUST expose a deterministic recovery outcome indicating that mission control is blocked because durable state is contradictory or insufficient to establish a safe owner/state.

### G7-5 — Durable traceability

The contradiction and safe-stop decision MUST remain attributable to the same mission and the relevant handoff, ownership epoch, task, and event identifiers.

### G7-6 — No duplicate execution

Entering the fail-closed state MUST NOT cause already-succeeded tasks to execute again or create another terminal mission outcome.

### G7-7 — Restart preserves the contradiction

Restarting the predecessor or successor MUST NOT erase the contradictory state or make the contradiction disappear through process-local reconstruction.

### G7-8 — Recovery requires explicit correction

A mission blocked by a contradiction MUST remain blocked until an explicitly authorized corrective transition is introduced in a later, separately scoped phase.

## 3. Required contradiction classes

The deterministic acceptance test MUST cover at least these classes:

1. Handoff / ownership mismatch: durable handoff indicates RELEASED or ACQUIRED for a designated successor while the ownership record still identifies an incompatible owner/epoch; recovery MUST stop Director-controlled mutation.
2. Ownership / successor mismatch: durable handoff identifies successor B at a fencing epoch that does not match the authoritative ownership record; neither predecessor nor successor may guess and continue.
3. Terminal / runnable-task contradiction: the mission is durably terminal while mission-control reconstruction still exposes an authoritative task state that would require additional Director-driven completion; recovery MUST not create a second terminal path or silently reopen the mission.
4. Post-restart persistence: the same contradiction is observed after constructing a fresh runtime/store instance from the existing durable state; restart MUST preserve the safe-stop behavior and evidence.

## 4. Acceptance method

The acceptance phase MUST use:
- deterministic clocks;
- controlled Director implementations;
- the existing DirectorOwnershipStore;
- the existing DirectorHandoffStore;
- the existing durable mission/task/event stores.

The acceptance test MAY construct contradictory durable fixtures directly in a test database when required to represent states that valid transitions intentionally prevent.

The test MUST verify both:
- the attempted Director mutation is rejected or blocked; and
- the durable records remain unchanged by the failed recovery attempt, apart from explicitly defined diagnostic or safe-stop events.

## 5. Safety boundaries

Gate 7 MUST NOT create a second source of truth.

The existing authorities remain:
- DirectorHandoffStore for handoff lifecycle;
- DirectorOwnershipStore for mission-level ownership and fencing;
- durable mission/task/event state for mission progress.

Gate 7 only defines what the runtime must do when those authorities cannot be reconciled safely.

## 6. Explicit non-goals

This gate does NOT authorize:
- automatic repair of contradictory durable state;
- automatic election or arbitrary successor selection;
- distributed consensus;
- provider/model failover;
- dashboard recovery controls;
- changes to worker lease ownership;
- new mission planning;
- live-authorization changes.

## 7. Phase boundary

This phase authorizes only:
- this contradiction/fail-closed contract;
- deterministic acceptance-test design;
- documentation of any implementation gap revealed by the acceptance phase.

No runtime behavior change is authorized until the contract is merged and a subsequent acceptance-test gate is separately opened.

## 8. Exit criteria

The Gate 7 contract phase closes only when:
- this contract is merged to canonical main;
- contradiction classes and required outcomes are explicit and reviewable;
- the subsequent deterministic acceptance test is defined as a separate gated step;
- no unrelated Aurora subsystem is modified.
