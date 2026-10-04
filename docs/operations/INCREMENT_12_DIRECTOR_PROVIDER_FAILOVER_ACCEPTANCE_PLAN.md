# Increment 12 — Provider Failover Acceptance Plan

## Canonical prerequisite

Increment 11 is closed on canonical main. Gate 8 remains complete.

## Acceptance sequence

1. Build a Gemini primary provider that returns a real successful Director response.
2. Inject a forced Gemini execution failure after the primary success.
3. Provide an eligible NVIDIA/Router alternative.
4. Verify the same provider request is served by the alternative without recreating Aurora state.
5. Verify the failover transition is durably recorded.
6. Verify subsequent Director calls use the replacement provider.
7. Verify durable conversation continuity.
8. Verify durable mission continuity and no duplicate completed work.
9. Force every candidate to fail and verify DirectorProviderFailoverError plus no activation.
10. Restart from the same runtime database and verify the successful replacement remains active.

## Canonical deterministic acceptance evidence

The deterministic provider, mission, conversation, fail-closed and restart-persistence acceptance suite is green on canonical main after PR #121:
- PR #121 merged as `69edf62465e6aebea6a064d23b06efc3df38058d`;
- canonical CI #2104 passed with `test` and `e2e-network` green.

## Deterministic E2E topology

    Gemini(primary fake success)
    -> forced Gemini failure
    -> NVIDIA/Router candidate
    -> same DurableTaskStore / conversation store
    -> terminal mission

The test must assert stable mission/task/session identifiers rather than merely asserting that a second provider returned text.

## Live verification

The existing LIVE-GEMINI gate remains the proof that the Gemini provider can execute against the real external API.

Increment 12 adds a dedicated live-failover gate only when two independent real provider credentials are available. It must force failure through an injectable failure boundary rather than intentionally corrupting a live request.

The live gate proves:

- real Gemini primary response;
- controlled failure injected after primary response;
- real alternative provider response;
- durable active-provider persistence;
- no credential leakage in failover evidence.

Workflow: `.github/workflows/live-director-provider-failover.yml`.
Environment: `director-provider-failover-live`.
Status after implementation merge: READY / NOT YET EXECUTED.

No live external execution authorization is activated by this gate.
