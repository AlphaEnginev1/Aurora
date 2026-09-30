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
- CI 1392: SUCCESS — Production Observability V1
- CI 1398/1399: SUCCESS — Recovery Drills V1
- CI 1414: SUCCESS — Controlled Live Authorization V1
- consolidation issue: #50 (CLOSED)

No new functionality was authorized during the consolidation gate; Increment 11 remains explicitly unapproved.

## Source of truth

The authoritative project-state record is:

`docs/AURORA_CONSOLIDATION_MATRIX.md`

The roadmap is reconciled to that record:

`ROADMAP.md`

The matrix distinguishes **IMPLEMENTADO**, **TESTEADO**, **RUNTIME INTEGRADO**, **LIVE VERIFICADO** and
**PENDIENTE REAL** so that a feature cannot be treated as production-ready merely because code or unit tests exist.

## Frozen production-readiness queue — COMPLETE

The ordered production-readiness blocks 1–7 are verified on canonical `main`:

1. LIVE-RESEARCH-DISCOVERY — VERIFIED
2. OS-NETWORK-ENFORCEMENT — VERIFIED
3. LIVE-NVIDIA — VERIFIED
4. LIVE-GEMINI — VERIFIED
5. PRODUCTION-OBSERVABILITY — VERIFIED
6. RECOVERY-DRILLS — VERIFIED
7. CONTROLLED-LIVE-AUTHORIZATION — VERIFIED

Block 7 provides the explicit live-operation authorization boundary. It does not grant live access automatically: Aurora remains fail-closed until a human operator supplies a valid, scoped, non-expired authorization record matching the verified canonical commit and prerequisites.

Historical branches and closed PRs are not alternate work queues. Increment 11 remains explicitly unapproved by the consolidation record.
