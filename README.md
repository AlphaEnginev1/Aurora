# Aurora

Autonomous multi-agent engineering system.

## Current status

**IMPLEMENTATION COMPLETE — DYNAMIC NVIDIA FREE CATALOG AUDIT IN PROGRESS**

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
- PR #149: Critical Decision Model Ensemble — MERGED
- PR #150: Multi-Agent Adversarial Review — MERGED
- PR #151: Provider Availability & Multi-Provider Resilience — MERGED
- current multi-provider LIVE acceptance: defined and ready for execution

`main` remains the single authoritative engineering line. Closed increments are not reopened.

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

Historical branches and closed PRs are not alternate work queues. Increment 11 and Increment 12 are closed and are not reopened by the current resilience work.


## Universal Work / Cowork architecture

The Universal Workbench is **IMPLEMENTED AND VERIFIED** on canonical `main`.

`docs/architecture/AURORA_UNIVERSAL_WORKBENCH_ARCHITECTURE.md`

The Director and every specialist agent can request the work capability required by the mission—local files/computer, documents and images, web/browser, research, GitHub, external connectors, code/tools and multimodal processing—through one governed Workbench. Final Director-led acceptance is recorded in `docs/architecture/AURORA_UNIVERSAL_WORKBENCH_ARCHITECTURE.md`.

The contract preserves least-privilege, approvals, sandboxing, credential isolation, provenance, evidence and auditability. It also explicitly preserves all previously closed acceptance boundaries.

## Current free-model resilience closure

The canonical Aurora runtime is now **free-only**. The active Director path uses the free Gemini tier, and specialist inference/failover uses the dynamic NVIDIA NIM Free Endpoint catalog under `config/free-model-policy.json`. The runtime rejects paid or unclassified models before routing, and the dedicated LIVE gate in `docs/operations/LIVE_MULTI_PROVIDER_RESILIENCE.md` requires only Gemini and NVIDIA credentials.\n\n### Dynamic NVIDIA model catalog
The checked-in NVIDIA entries are only known-good seed metadata. At runtime Aurora joins the live NVIDIA `/v1/models` serving catalog with NVIDIA's official Build catalog labels, where `Free Endpoint` is the authoritative free-access signal. Every currently served NVIDIA Free Endpoint can therefore enter Aurora dynamically; the seed list is never a ceiling. Models are then routed according to the execution capability their provider adapter supports.