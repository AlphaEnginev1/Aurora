# Aurora

Autonomous multi-agent engineering system.

## Current status

**CONSOLIDATION GATE — FINAL RECORD**

Aurora has one authoritative engineering line:

- **canonical branch:** `main`
- canonical consolidation commit: `e4dff9e0e15198d41cb5f3e086903fecd4e9d9f6`
- consolidation evidence branch: `runtime/consolidation-gate`
- pre-consolidation main preserved as: `archive/main-pre-consolidation-20260929`
- CI 1287–1289: SUCCESS
- CI 1291–1296: SUCCESS
- consolidation issue: #50

No new functionality has been authorized during the consolidation gate.

## Source of truth

The authoritative project-state record is:

`docs/AURORA_CONSOLIDATION_MATRIX.md`

The roadmap is reconciled to that record:

`ROADMAP.md`

The matrix distinguishes **IMPLEMENTADO**, **TESTEADO**, **RUNTIME INTEGRADO**, **LIVE VERIFICADO** and
**PENDIENTE REAL** so that a feature cannot be treated as production-ready merely because code or unit tests exist.

## Genuine remaining production work

The remaining gaps are finite and evidence-backed:

1. governed runtime wiring for the existing research `HttpSearchProvider`;
2. decision and proof of OS-level transparent network enforcement;
3. real NVIDIA provider verification;
4. real Gemini provider verification;
5. production observability;
6. controlled recovery/crash drills;
7. explicit controlled production authorization.

**Increment 11 is not authorized by the consolidation record.**

New capability work resumes only after the consolidation gate is explicitly closed and the next increment is
derived from the remaining evidence.
