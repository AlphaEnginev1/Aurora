# Aurora Governance

The repository is the canonical engineering record. Architecture changes require an ADR. Security, network and privilege
changes require tests and explicit review. No secrets are committed. Failed verification blocks capability activation.
Live/production execution is a separate authorization stage.

## Consolidation Gate — CLOSED

The canonical engineering branch is **`main`**.

The authoritative project-state record is `docs/AURORA_CONSOLIDATION_MATRIX.md`. Historical implementation branches are
archival evidence, not alternate work queues.

The consolidation gate is now closed. Historical branches are archival evidence, `main` is canonical, and production-readiness gaps are enumerated in the consolidation matrix. No new capability is authorized merely by the closure of this gate.

See:
- `docs/CONSOLIDATION_GATE.md`
- `docs/AURORA_CONSOLIDATION_MATRIX.md`
- Issue #50
