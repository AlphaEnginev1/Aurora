# Aurora

Autonomous multi-agent engineering system.

## Current status

**CONSOLIDATION COMPLETE — FINAL RECORD**

Aurora has one authoritative engineering line:

- **canonical branch:** `main`
- consolidation evidence branch: `runtime/consolidation-gate`
- pre-consolidation main preserved as: `archive/main-pre-consolidation-20260929`
- CI 1287–1289: SUCCESS
- CI 1291–1296: SUCCESS
- CI 1305: SUCCESS — research discovery E2E
- CI 1306: SUCCESS — canonical `main`
- consolidation issue: #50 (CLOSED)

No new functionality was authorized during the consolidation gate; Increment 11 remains explicitly unapproved.

## Source of truth

The authoritative project-state record is:

`docs/AURORA_CONSOLIDATION_MATRIX.md`

The roadmap is reconciled to that record:

`ROADMAP.md`

The matrix distinguishes **IMPLEMENTADO**, **TESTEADO**, **RUNTIME INTEGRADO**, **LIVE VERIFICADO** and
**PENDIENTE REAL** so that a feature cannot be treated as production-ready merely because code or unit tests exist.

## Genuine remaining production work

The remaining gaps are finite and evidence-backed:

1. live verification against the intended real research search provider;
2. decision and proof of OS-level transparent network enforcement;
3. real NVIDIA provider verification;
4. real Gemini provider verification;
5. production observability;
6. controlled recovery/crash drills;
7. explicit controlled production authorization.

**Increment 11 is not authorized by the consolidation record.**

New capability work resumes only after the consolidation gate is explicitly closed and the next increment is
derived from the remaining evidence.
