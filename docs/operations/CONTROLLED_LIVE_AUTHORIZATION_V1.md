# Controlled Live Authorization V1

**Status:** TEST CONTRACT

Block 7 establishes the explicit human-controlled gate between Aurora's verified engineering/runtime state and live/production execution.

## Existing boundary

Aurora already has:
- provider-neutral ModelRouter;
- governed InternetGateway and OS-level network enforcement;
- verified live research/NVIDIA/Gemini gates;
- production observability;
- crash/restart/recovery drills;
- durable Director/TaskEngine runtime.

This block does not reimplement any of those systems.

## Authorization model

Live execution is denied by default.

A caller may request live mode only through an explicit authorization record containing:
- authorization_id;
- approved_by;
- issued_at;
- expires_at;
- explicit non-empty scope;
- prerequisite evidence states for Blocks 1–6;
- live_enabled=true;
- revoked=false;
- kill_switch=false.

The authorization gate returns an immutable decision/snapshot. It does not mutate Director decisions, task state, routing, learning state, or provider configuration.

## Scope

The authorization scope is allowlisted and operation-specific. V1 uses:
- director_live_execution;
- live_external_research;
- live_external_inference;
- live_capability_acquisition.

A live request must declare its operation and the operation must be present in the authorization scope.

## Fail-closed rules

Authorization is denied when:
- no record is supplied;
- live_enabled is false;
- the record is revoked or kill-switched;
- the record is expired or issued in the future beyond clock-skew tolerance;
- approved_by or authorization_id is missing;
- scope is empty or contains unknown operations;
- any prerequisite Block 1–6 is not VERIFIED;
- the record contains credential material;
- the requested operation is outside scope.

## Runtime integration

The existing daemon remains unchanged for normal/non-live operation.

When live mode is explicitly requested (--live), the daemon must require a valid authorization record. A failed authorization prevents the runtime from entering live mode and exits non-zero before any live mission execution.

No authorization is persisted automatically. Creation/revocation of the authorization record remains an explicit human action outside the runtime.

## Evidence

V1 records:
- authorization identity and scope;
- prerequisite evidence state references;
- issue/expiry times;
- canonical runtime commit supplied by the caller;
- authorization decision reason.

It must never log API keys, bearer tokens, passwords, or provider credentials.

## Closure

Block 7 is VERIFIED only after:
1. contract tests are green;
2. live mode is fail-closed without explicit authorization;
3. authorized operation/scope succeeds through the existing runtime entry point;
4. expired/revoked/kill-switched/insufficient-prerequisite records are denied;
5. no authorization path changes normal Director behavior;
6. full test and e2e-network CI are green on the canonical main line.
