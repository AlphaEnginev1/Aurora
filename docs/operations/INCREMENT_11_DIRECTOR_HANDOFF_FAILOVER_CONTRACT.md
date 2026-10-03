# Increment 11 — Director Handoff / Failover Contract

**Status:** CONTRACT + E2E PROOF PHASE  
**Canonical base:** `main` at `08fd57d`  
**Scope:** Director replacement during an in-progress durable mission.  
**Exclusion:** No unrelated Dashboard, Model Registry, provider, research, or frozen production-readiness work.

## 1. Objective

Prove and then formalize that an in-progress Aurora mission can survive loss or replacement of its active Director without losing authoritative state, repeating completed work, or creating a second competing mission plan.

The capability is specifically:

```
Director A
  -> persisted mission/task/event state
  -> Director A unavailable
  -> Director B starts against the same durable state
  -> B reconstructs authoritative context
  -> B continues unfinished work
  -> B does not repeat completed work
  -> mission reaches the same terminal state
```

Changing the configured Director provider/model in SETUP is **not** itself the handoff contract. The contract concerns an in-flight mission whose active Director instance is replaced.

## 2. Authoritative state

A handoff MUST use persisted Aurora state as the source of truth. The replacement Director MUST NOT infer completion from process memory or conversational history alone.

The handoff state includes, at minimum:
- mission identity and objective;
- durable task graph and task status;
- dependency relationships;
- execution/attempt history;
- durable events relevant to the mission;
- authoritative mission context available to the Director;
- decision/review state already persisted by Aurora.

## 3. Required invariants

### H1 — Same mission

Director B continues the existing mission identifier. It MUST NOT create a second mission for the same handoff.

### H2 — No duplicate completed work

A task already durably marked `succeeded` MUST NOT be executed again solely because Director A was replaced.

### H3 — Continue unfinished work

Pending/recoverable tasks remain eligible for execution after takeover, respecting their persisted dependencies and retry/lease policy.

### H4 — Reconstruct authoritative context

Director B MUST be able to obtain the persisted mission objective/context required for review and continuation.

### H5 — No re-planning of the existing graph

Director B MUST NOT discard the durable task graph and generate an unrelated replacement graph for the same mission.

### H6 — Single terminal outcome

The mission MUST produce one authoritative terminal outcome, without a second success/failure event caused only by the handoff.

### H7 — Fail closed on ambiguity

If the durable state is missing, contradictory, or cannot establish ownership/continuation safely, takeover MUST stop rather than guessing.

### H8 — Traceability

The handoff MUST remain observable through mission/task/event identifiers so that the transition from Director A to Director B can be reconstructed.

## 4. Acceptance E2E

The contract proof MUST execute this sequence:

1. Director A creates a two-step mission.
2. Director A completes step A only.
3. Step B remains pending and depends on step A.
4. Director A is removed from the active runtime path.
5. Director B starts using the same durable task/event state.
6. Director B does not call the planning path for a new graph.
7. Director B sees the persisted mission context.
8. Director B causes only step B to execute.
9. The mission reaches `succeeded`.
10. The execution history contains exactly one execution of each step.

The deterministic E2E test in `tests/e2e/test_director_handoff_failover.py` is the first acceptance gate. It intentionally uses controlled Directors/execution so the handoff semantics are isolated from external provider availability.

## 5. Phase boundary

This first phase authorizes only:
- the contract above;
- deterministic E2E acceptance proof;
- documentation of observed gaps and evidence.

It does **not** yet authorize:
- a new handoff API;
- automatic Director election;
- automatic provider failover;
- background leader election;
- live multi-Director concurrency;
- dashboard controls for Director takeover.

Those are implementation decisions that require the E2E contract to be established first.

## 6. Current evidence

A local controlled probe executed on canonical `08fd57d` passed:

```
DIRECTOR_HANDOFF_PROBE: PASS
executions: ['step_a', 'step_b']
director_A_plan_calls: 1
director_B_review_calls: 1
director_B_reused_persisted_context: YES
director_B_reexecuted_completed_step_a: NO
mission_status: succeeded
```

This is evidence that the existing durable runtime primitives can satisfy the core continuation scenario. It is not yet a formal production handoff mechanism.

## 7. Exit criteria for the contract phase

This phase closes only when:
- the contract is merged to canonical `main`;
- the deterministic E2E test passes in CI;
- the test proves H1–H6 above;
- no production implementation changes are included in this phase.

The next phase, only after this gate passes, may address an explicit operational handoff/failover mechanism.
