# Dashboard Full E2E Proof Plan

**Status:** COMPLETE — Block 13  
**Canonical base:** main  
**Scope:** prove the integrated dashboard/Director/avatar boundary without reopening the closed production-readiness queue.

## Evidence already present

| Boundary | Existing evidence | Classification |
|---|---|---|
| Dashboard HTTP session + turn + history | `tests/test_dashboard_conversation_api.py::test_http_conversation_round_trip` | Automated, fake provider |
| Durable Director conversation service | `tests/test_director_conversation.py` | Automated |
| Core Director/research/task/learning cycle | `tests/e2e/test_aurora_core_e2e.py` | Deterministic integration |
| Browser voice adapter contracts | `tests/test_avatar_voice_contract.py` | Contract tests; not real microphone/browser E2E |
| Dashboard UI contract | `tests/test_dashboard_ui_contract.py` | Static/UI contract |
| Dashboard polling/reconciliation | Block 11 merged; 10-second polling and backoff | Implemented; needs recovery proof |
| Canonical portrait adapter | Block 12F merged; URL configured through `AURORA_AVATAR_PORTRAIT_URL` | Adapter implemented; exact asset not yet confirmed bundled/served |

## Required proof lanes

### 13A — Integrated conversation lifecycle
Run the dashboard HTTP server against a constructed Aurora runtime and a controlled provider. Prove create session → submit user turn → Director bootstrap/Context Gate → provider execution → assistant response → durable history retrieval. Assert correlation, model/provider/execution identity, and no direct provider call from browser code.

### 13B — Voice-to-Director handoff
Prove a final speech transcript enters the same `sendConversationMessage` path as typed text; response is passed to speech presentation; microphone/speech failure preserves text interaction. Browser API mocks are acceptable for CI; label this as mocked browser behavior, not physical microphone validation.

### 13C — Restart and reconciliation
Prove conversation history survives service/server restart; dashboard re-fetches authoritative snapshot after reconnect; stale event/snapshot data cannot overwrite newer authoritative state. Identify the current polling-only limitation accurately.

### 13D — Canonical portrait asset
Confirm the exact user-designated Aurora image is available at a stable served path, loads in the central portrait slot, and state transitions animate it. Never substitute another image. The screenshot wrapper must not be used as the face asset if the stored original portrait is available.

### 13E — Real provider gate
Keep deterministic fake-provider CI separate from a controlled live Gemini/provider proof. Record provider/model, execution/correlation IDs, response success, and safety boundary; do not expose credentials or claim live verification from fake-provider tests.

### 13F — Final acceptance
Publish a result table with separate statuses: IMPLEMENTED, TESTED, RUNTIME INTEGRATED, LIVE VERIFIED, PENDING REAL. Block completion if any mandatory lane is merely inferred from unit tests.

## Execution order

1. Inspect existing test seams and add the smallest integrated lifecycle proof (13A).
2. Add voice handoff and failure-fallback proof (13B).
3. Add restart/reconciliation proof (13C).
4. Wire and verify the canonical image asset (13D).
5. Run live provider proof only under the existing authorization and provider gates (13E).
6. Report evidence and unresolved limits (13F).

Do not interpret this plan as evidence that any lane has passed.


## Final acceptance — 13F

Block 13 is CLOSED on canonical `main`.

| Lane | IMPLEMENTADO | TESTEADO | RUNTIME INTEGRADO | LIVE VERIFICADO | PENDIENTE REAL |
|---|---|---|---|---|---|
| 13A — Integrated conversation lifecycle | YES | YES | YES | N/A | None identified |
| 13B — Voice-to-Director handoff | YES | YES | YES | N/A | No browser hardware validation claim |
| 13C — Restart and reconciliation | YES | YES | YES | N/A | None identified in this proof lane |
| 13D — Canonical portrait asset | YES | YES | YES | Operator-validated | None identified |
| 13E — Real provider gate | YES | YES | YES | YES — Gemini 3.8 Flash, 4/4 E2E | None identified |
| 13F — Final acceptance | YES | YES | YES | YES | None |

13A–13D are supported by the merged dashboard proof PRs and their deterministic integration/contract evidence. 13D additionally received operator visual acceptance in the local dashboard. 13E is supported by the real Gemini provider E2E gate with all four tests passing.

The distinction between mocked browser behavior and real external-provider verification is retained. No credential, provider secret, or production live-authorization record is stored in the repository.

**Block 13 outcome: COMPLETE.**

No new feature increment is opened by this closure. Increment 11 remains outside the authorized work queue.
