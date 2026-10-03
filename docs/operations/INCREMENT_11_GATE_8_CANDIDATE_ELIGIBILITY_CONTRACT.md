# Increment 11 — Gate 8 Block 2: Candidate Eligibility Authority Contract

**Status:** CONTRACT PHASE — NO IMPLEMENTATION
**Canonical prerequisite:** Gate 8 contract and Acceptance Block 1 are complete on canonical main.
**Scope:** Define the authoritative eligibility state that permits a Director runtime to participate in automatic mission failover.
**Exclusions:** No automatic election runtime changes, distributed consensus, multi-machine discovery, provider/model failover, dashboard takeover controls, worker lease redesign, or live-authorization changes.

## 1. Objective

Automatic failover requires an explicit answer to one question before a Director may contend for an expired mission:

**Is this Director currently eligible to take control?**

Eligibility MUST come from an authoritative source. A Director MUST NOT self-declare eligibility merely because it can read an expired ownership lease.

## 2. Required invariants

### E2-1 — Unique Director identity
Every candidate has one stable runtime identity. Two active records MUST NOT represent the same Director identity with contradictory state.

### E2-2 — Explicit eligibility
Eligibility is an explicit state, not inferred from process existence, network reachability, or possession of a local configuration value.

### E2-3 — Capability requirements
The eligibility record MUST identify the capabilities required for Director mission control, including access to the durable mission/ownership authority.

### E2-4 — Readiness and freshness
The source MUST distinguish eligible/ready candidates from stale or explicitly ineligible candidates.

### E2-5 — Restart persistence
A Director restart MUST reconstruct its eligibility identity/state from the authoritative source rather than create a new arbitrary identity.

### E2-6 — No self-promotion
A Director with no authoritative eligibility record MUST NOT enter the automatic takeover candidate set.

### E2-7 — Fail closed on ambiguity
If the eligibility source is unavailable, contradictory, or cannot establish a unique current state for a candidate, that candidate MUST NOT participate in takeover.

### E2-8 — Ownership remains separate
Eligibility answers whether a Director may contend. DirectorOwnershipStore remains the sole authority that decides current mission ownership.

### E2-9 — Eligibility does not bypass cooperative handoff
An eligible Director MUST NOT bypass REQUESTED, QUIESCING, RELEASED, or ACQUIRED cooperative handoff state.

### E2-10 — Eligibility does not activate live authorization
Eligibility for Director control MUST NOT grant external live-execution authorization.

## 3. Required eligibility record

The subsequent implementation design MUST define at minimum:

- stable Director identity;
- eligibility state;
- required capabilities;
- readiness/health freshness indicator;
- registration/update time;
- last-seen or heartbeat information;
- version or generation sufficient to detect stale records;
- explicit ineligibility/removal state.

Sensitive provider credentials MUST NOT be stored in eligibility records.

## 4. Acceptance cases

The deterministic acceptance phase MUST cover at least:

1. eligible B and ineligible C observe the same expired mission; only B may contend;
2. B transitions from eligible to ineligible before takeover; B must not acquire ownership;
3. a stale eligibility record is rejected after its freshness boundary;
4. the authoritative eligibility source is unavailable; takeover fails closed;
5. contradictory records for one Director identity fail closed;
6. Director restart preserves the same stable identity and eligibility state;
7. eligibility and mission ownership remain separate authorities;
8. cooperative handoff cannot be bypassed by an eligible candidate.

## 5. Selection and race boundary

Eligibility MUST only gate candidate participation.

When multiple eligible candidates contend, the existing DirectorOwnershipStore atomic acquisition remains the final ownership authority. Eligibility MUST NOT add a second election or ownership decision.

## 6. Phase boundary

This phase authorizes only:
- the eligibility contract;
- deterministic acceptance-test design;
- documentation of implementation gaps.

No runtime implementation, automatic candidate discovery, or election behavior is authorized until this contract is merged and its separate acceptance phase is opened.

## 7. Exit criteria

This contract phase closes only when:
- the contract is merged to canonical main;
- eligibility identity, state and freshness semantics are explicit;
- the subsequent deterministic acceptance phase is separately scoped;
- no unrelated Aurora subsystem is modified.
