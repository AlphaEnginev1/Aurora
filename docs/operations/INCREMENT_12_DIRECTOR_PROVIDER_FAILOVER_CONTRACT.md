# Increment 12 — Director Provider Failover / Replacement Contract

**Status:** CONTRACT COMPLETE — implementation follows on the dedicated branch.  
**Canonical prerequisite:** Increment 11 is COMPLETE on main.  
**Scope:** Automatic replacement of the active Director provider after a provider execution failure, while preserving authoritative Aurora state.

## 1. Objective

Aurora must be able to continue Director planning, review, and conversation when the active Director provider becomes unavailable.

Target lifecycle:

    Gemini ACTIVE -> provider failure -> failure detection -> eligible alternative -> replacement ACTIVE -> same Aurora state -> continuation

The provider failover mechanism is independent from Director mission ownership/fencing. Increment 11 remains closed and is not reopened.

## 2. Invariants

1. The active provider is the only provider used until an actual provider execution failure occurs.
2. A provider candidate becomes active only after its first request succeeds.
3. Provider replacement never creates a new mission, task graph, project, knowledge store, or decision history.
4. Existing durable conversation session IDs remain unchanged.
5. Existing durable mission IDs, task IDs, knowledge IDs and decision IDs remain unchanged.
6. Completed work is not re-executed solely because provider replacement occurred.
7. Every detected failure and every replacement attempt has a correlation ID and durable evidence.
8. If no eligible provider succeeds, Director operation fails closed; Aurora does not invent or silently continue with an unvalidated provider.
9. Provider credentials never enter failover events or durable failover records.
10. Mission ownership and fencing remain under DirectorOwnershipStore; provider failover has no authority over ownership.

## 3. Eligible alternatives

The initial implementation supports the already integrated Aurora provider paths:

- router — provider-neutral inference through ModelRouter;
- nvidia_api — inference through the same ModelRouter with an eligible NVIDIA model;
- gemini — current primary provider and not a fallback to itself.

Candidate order is operator-configurable. The default order after a Gemini failure is nvidia_api, then router.

An alternative is eligible only when its provider credentials/configuration are available and, for inference-router candidates, at least one enabled/healthy/available reasoning model exists.

## 4. Failure boundary

Provider failover is triggered only by exceptions raised while executing a provider request. Provider configuration errors discovered while constructing a fallback candidate are recorded as candidate failures.

For Gemini:

- transient HTTP and transport failures are failoverable;
- invalid or empty provider responses are failoverable;
- non-transient client errors such as an invalid request are fail-closed for that request and are not silently masked by provider switching.

The provider adapter exposes this distinction through a failoverable attribute on provider execution errors.

## 5. Durable activation

A successful replacement updates the local operator Director provider setting only after the replacement response succeeds.

This makes the replacement survive process restart and prevents a failed candidate from becoming the persisted active Director.

## 6. Conversation continuity

Provider failover is transparent to the durable conversation service:

- same conversation session;
- same turn sequence;
- same Aurora bootstrap/context contract;
- only provider/model identity changes on the successful replacement turn.

The old provider is never reintroduced automatically unless the operator explicitly switches back or a future governed policy authorizes it.

## 7. Mission continuity

For an active durable mission:

- the mission ownership epoch is unchanged;
- task IDs remain unchanged;
- completed tasks remain completed;
- the replacement Director receives the same mission-derived context;
- the next review/planning operation continues the current mission rather than recreating it.

## 8. Evidence

For every failover attempt Aurora records:

- transition ID;
- failover correlation ID;
- phase;
- Director role;
- source provider/model;
- candidate provider/model;
- error type/message where applicable;
- creation/completion timestamps.

Operational events additionally carry mission context when a mission operation is active.

## 9. Fail-closed

If all eligible candidates fail, Aurora raises a dedicated DirectorProviderFailoverError, records an exhausted transition, and performs no provider activation and no mission/task mutation attributable to the failed provider call.

## 10. Acceptance

The implementation is accepted only when these deterministic proofs pass:

### A. Provider failover

Gemini succeeds -> Gemini is forced to fail -> NVIDIA/Router succeeds -> the same request receives the alternative response -> active provider becomes the alternative.

### B. Mission continuity

A durable mission is started under Gemini -> a later Director review fails in Gemini -> alternative Director succeeds -> the same mission reaches its original terminal outcome with no duplicate completed work.

### C. Conversation continuity

A durable conversation session receives a successful Gemini turn -> Gemini fails -> alternative provider answers -> same session remains active and turn sequence is monotonic.

### D. Fail closed

Gemini fails -> every eligible alternative fails -> failover exhausts -> no new active provider is persisted.

### E. Restart persistence

A successful automatic replacement persists its active provider -> a fresh runtime loads the replacement provider without requiring manual repair.

### Explicit non-goals

- reopening Increment 11;
- changing Director ownership or fencing;
- distributed consensus;
- worker lease redesign;
- automatic live authorization;
- changing model-learning policy;
- dashboard takeover election;
- automatic switching back to a recovered provider.
