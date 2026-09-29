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

## Current block

**BLOCK 1 — LIVE-RESEARCH-DISCOVERY**

Current verified state:
- implementation: COMPLETE;
- automated tests: COMPLETE;
- runtime wiring: COMPLETE;
- controlled HTTP E2E: COMPLETE (CI 1305);
- canonical-main verification after merge: COMPLETE (CI 1310/1312 sequence);
- real external search-provider verification: **PENDING**.

Close condition:
- one real external search provider is selected;
- Aurora reaches it through the governed InternetGateway path;
- the provider returns usable discovery results through the existing HttpSearchProvider contract;
- the resulting source is retrieved and persisted by ResearchService;
- evidence is archived with exact provider/run/configuration details;
- the final CI/live result is recorded here and in docs/AURORA_CONSOLIDATION_MATRIX.md.

Do not start Block 2 until this close condition is satisfied.
