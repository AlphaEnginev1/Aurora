# Increment 11 — Gate 8 Block 2: Candidate Eligibility Authority Contract

**Status:** CLOSED — implemented and accepted on canonical `main`  
**Canonical evidence commit:** `de3d6ace8e45bd4f2741a6f9965ce3b4caaea75b`  
**Acceptance:** All eight G8-EA scenarios are covered by deterministic tests and pass in the merged implementation.

## 1. Objective

Automatic failover requires an explicit answer to one question before a Director may contend for an expired mission:

**Is this Director currently eligible to take control?**

Eligibility MUST come from an authoritative source. A Director MUST NOT self-declare eligibility merely because it can read an expired ownership lease.

Stable runtime identity is a separate bootstrap concern: the deployment/runtime supplies the Director identity and MUST reuse that identity across restarts. The eligibility registry is authoritative for eligibility state, readiness, freshness and capabilities; it is not a global directory from which a runtime may guess which Director process it is.

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
A Director restart MUST reuse the same stable runtime identity supplied by its deployment/runtime configuration. The runtime MUST NOT silently generate a new identity on restart when operating under the authoritative eligibility flow. Eligibility state itself remains durable in the authoritative eligibility store.

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

The implementation defines at minimum:

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

The deterministic acceptance phase covers:

1. eligible B and ineligible C observe the same expired mission; only B may contend;
2. B transitions from eligible to ineligible before takeover; B must not acquire ownership;
3. a stale eligibility record is rejected after its freshness boundary;
4. the authoritative eligibility source is unavailable; takeover fails closed;
5. contradictory records for one Director identity fail closed;
6. Director restart preserves the same stable identity and eligibility state;
7. eligibility and mission ownership remain separate authorities;
8. cooperative handoff cannot be bypassed by an eligible candidate.

## 5. Selection and race boundary

Eligibility only gates candidate participation.

When multiple eligible candidates contend, the existing DirectorOwnershipStore atomic acquisition remains the final ownership authority. Eligibility MUST NOT add a second election or ownership decision.

## 6. Implementation boundary

The accepted implementation contains only the narrow authority required for Gate 8 Block 2:

- durable `DirectorEligibilityStore` state;
- deterministic evaluation of state, readiness, capabilities and freshness;
- runtime gating immediately before expired-ownership takeover;
- explicit factory wiring of the eligibility authority;
- regression coverage preserving initial acquisition, active-owner restart and cooperative handoff behavior.

The implementation does not modify distributed consensus, provider/model failover, dashboard takeover controls, worker lease semantics, live authorization, external execution or a second ownership authority.

## 7. Closure evidence

The following evidence is authoritative for closure:

- **PR #115:** initial Candidate Eligibility Authority implementation merged to canonical `main`.
- **PR #116:** production wiring correction merged to canonical `main`; head commit `e83fa5b2c014ed39a7489df6e706134ea7ff440f`; merge commit `de3d6ace8e45bd4f2741a6f9965ce3b4caaea75b`.
- **Canonical CI #2037:** completed with conclusion `success` on `de3d6ace8e45bd4f2741a6f9965ce3b4caaea75b`.
- **CI #2037 — `test`:** success.
- **CI #2037 — `e2e-network`:** success.
- The merged acceptance suite covers G8-EA-1 through G8-EA-8.
- The runtime gate applies eligibility only to expired historical ownership where the configured production runtime has an eligibility authority; active-owner renewal and cooperative handoff remain on their existing authoritative paths.
- `DirectorOwnershipStore` remains the sole mission-ownership/fencing authority.

## 8. Gate status

**Gate 8 Block 2 — CLOSED.**

No further implementation is authorized under this block. Subsequent Gate 8 work, if any, must be opened as a separately scoped increment and must not re-enter this acceptance boundary.
