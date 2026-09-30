# Aurora — Production Readiness Queue

This file is the **single ordered execution queue** for production readiness. It is intentionally separate from
feature increments.

## Order — do not skip

1. **LIVE-RESEARCH-DISCOVERY** — verify the existing governed research discovery path against the intended real
   external search provider and archive the evidence.
2. **OS-NETWORK-ENFORCEMENT** — decide the required host boundary and execute the proof if required.
3. **LIVE-NVIDIA** — execute and archive real NVIDIA provider evidence.
4. **LIVE-GEMINI** — execute and archive real Gemini provider evidence.
5. **PRODUCTION-OBSERVABILITY** — complete operational visibility and alerting evidence.
6. **RECOVERY-DRILLS** — execute controlled crash/restart/reconciliation drills and archive evidence.
7. **CONTROLLED-LIVE-AUTHORIZATION** — establish the explicit production/live authorization gate.

## Queue invariants

- Work is strictly one block at a time.
- A later block must not be started while the current block is not closed with evidence.
- “IMPLEMENTADO”, “TESTEADO”, “RUNTIME INTEGRADO” and “LIVE VERIFICADO” are distinct states.
- A successful unit/E2E test does not count as LIVE verification.
- A workflow definition does not count as LIVE verification.
- No item in this queue is a license to reopen an already-integrated Aurora feature.
- No new capability increment is implied by this queue.
- Historical branches and old PRs are not alternative work queues.

## Canonical completion status

**FROZEN PRODUCTION-READINESS QUEUE 1–7 — COMPLETE**

All seven blocks are verified on the canonical `main` line:

1. **LIVE-RESEARCH-DISCOVERY** — VERIFIED
2. **OS-NETWORK-ENFORCEMENT** — VERIFIED
3. **LIVE-NVIDIA** — VERIFIED
4. **LIVE-GEMINI** — VERIFIED
5. **PRODUCTION-OBSERVABILITY** — VERIFIED
6. **RECOVERY-DRILLS** — VERIFIED
7. **CONTROLLED-LIVE-AUTHORIZATION** — VERIFIED

The final canonical production-readiness commit is `f0c60a01600d0f7e508508f29faefc516b763bbe` plus the documentation-only verification update that follows it.

## Block 7 status

The Controlled Live Authorization gate is established and enforced at the daemon entrypoint.

The gate is fail-closed and requires:
- an explicit human authorization record;
- verified prerequisites for Blocks 1–6;
- an operation-specific scope;
- a non-expired authorization;
- a matching canonical commit;
- no revocation or active kill-switch.

**Live operation is not automatically enabled by closing this queue.** No authorization record or provider credential is stored in the repository. Without an explicit valid human authorization record, `--live` is denied before the runtime is constructed.

## Queue closure rule

This frozen queue is now closed. Historical branches and previous PRs are not alternative implementation queues.

No Block 1–7 work should be restarted unless new evidence identifies a regression or a separately authorized production requirement. Increment 11 remains outside this queue and is not authorized by the consolidation record.

