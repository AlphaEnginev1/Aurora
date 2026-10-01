# Aurora Dashboard ↔ Aurora Finalization Worklist

**Status:** ARCHITECTURE GATE  
**Canonical runtime branch:** main  
**Dashboard branch:** architecture/dashboard-final-v1

This is the single worklist derived from the final dashboard architecture and the audit of canonical main. It does not reopen the previously closed production-readiness queue.

## 1. Already present and KEEP

| Capability | Current state | Action |
|---|---|---|
| Organization Registry | Implemented / tested / runtime integrated | Keep |
| Agent selection | Implemented / tested / runtime integrated | Keep |
| Dynamic teams | Implemented / tested / runtime integrated | Keep |
| Skill Registry | Implemented / tested / runtime integrated | Keep |
| Skill evaluation | Implemented / tested / runtime integrated | Keep |
| Skill learning/evolution | Implemented / tested / runtime integrated | Keep |
| ModelRouter | Implemented / tested / runtime integrated | Keep |
| ModelPerformanceStore | Implemented / tested / runtime integrated | Keep |
| Provider-neutral inference contract | Implemented | Keep |
| NVIDIA provider | Live verified | Keep |
| Gemini Director path | Live verified | Keep |
| Research evidence store | Implemented / live verified | Keep |
| Trusted knowledge + citation graph | Implemented / live verified | Keep |
| Durable Event Bus | Implemented / tested / runtime integrated | Extend, do not replace |
| MissionMemory | Implemented / tested / runtime integrated | Evolve into shared context plane |
| Director 24/7 runtime | Implemented / tested / runtime integrated | Keep |
| Observability/recovery | Production verified | Keep |

## 2. Missing architectural foundations

### P0 — Aurora Knowledge/Context Plane
Create a common context contract and mandatory Context Gate used by Director and all project model executions.

Required outputs:
- context manifest
- scope/project identity
- freshness
- provenance
- authoritative vs generated classification
- deterministic unknown/missing behavior

### P0 — Project Registry
Create the authoritative project catalog.

Minimum project record:
- project_id
- name
- objective
- status
- repository/workspace reference
- architecture reference
- active agents
- active skills
- active models
- tasks/decisions indexes
- current state
- created/updated timestamps

### P0 — Director Bootstrap
Create the startup contract that loads Aurora identity, project index, organization, runtime state, mailbox, active decisions, knowledge/evidence index and Director model policy before conversation.

### P0 — Universal model context enforcement
Make Context Gate a prerequisite to inference for Director and specialist/project models. No provider adapter should accept an Aurora work request without the context contract.

### P1 — Provider Registry
Formalize provider identities and adapters separately from model definitions.

### P1 — Model Registry
Formalize the model catalog and its metadata. Existing ModelSpec can become the initial record contract.

### P1 — Model discovery/synchronization
Add provider-specific discovery adapters where supported. Discovered candidates remain inactive until policy/security/health validation succeeds.

### P1 — Shared mailbox/context evolution
Unify the concepts represented by Event Bus + MissionMemory without creating a duplicate queue/journal. Add global/project/agent views and correlation metadata.

## 3. Dashboard backend

Build a Dashboard Gateway/Read API as a boundary over Aurora stores/runtime.

Required read domains:
- system summary
- projects
- agents
- agent details
- tasks
- mailbox
- events
- models/providers
- skills/learning
- research/knowledge
- observability/recovery
- audit/governance

Required command domains:
- send Director message
- create/approve/reject governed requests
- pause/resume where policy permits
- inspect and acknowledge operational events where authorized

The dashboard must not directly mutate Aurora persistence.

## 4. Dashboard frontend

Build the Operations Center with these views:
1. Director Core + central animated portrait
2. orbital agent workspace around the mailbox
3. Agent Inspector
4. Project Inspector
5. Model Intelligence
6. Skills & Learning
7. Research & Knowledge
8. Runtime/Recovery
9. Audit/Governance

The visual topology is a representation of Aurora state; it is not an independent organization model.

## 5. Live synchronization

Implement snapshot + incremental event updates + reconciliation.

Acceptance criteria:
- reconnect without losing authoritative state
- stale/out-of-order events do not corrupt displayed state
- freshness is visible
- event correlation permits drill-down from UI -> event -> task/execution -> evidence

## 6. Avatar integration

Add an Avatar Adapter for the central portrait.

Acceptance criteria:
- conversation state remains in Aurora
- avatar provider can be replaced
- speech/animation failure does not affect Director execution
- dashboard can fall back to static portrait/text

## 7. Final E2E proof

The project is not considered finished until these proofs pass:

### A. Director replacement
Gemini Director -> alternate provider Director -> same Aurora state -> same projects/tasks/knowledge/decisions.

### B. Model replacement
Project model A -> project model B -> shared Context Gate -> continued learning and routing history.

### C. New model onboarding
Provider discovery -> validation -> Model Registry -> ModelRouter -> controlled execution -> evaluation -> ModelPerformanceStore.

### D. Agent inspection
Task execution -> Event Bus -> dashboard -> click agent -> exact task/model/skill/evidence state.

### E. Project inspection
Project state -> dashboard -> project inspector -> authoritative tasks/decisions/knowledge.

### F. Recovery
Dashboard disconnect/restart -> authoritative snapshot -> event replay/reconciliation -> correct live state.

## 8. Finalization order

Do the work in this order and do not jump ahead:

```
1. Project Registry
2. Aurora Context Plane / Context Gate
3. Director Bootstrap
4. Universal model-context enforcement
5. Provider Registry
6. Model Registry
7. Model Discovery/Sync
8. Mailbox/Event Bus evolution
9. Dashboard Gateway / Read Model
10. Dashboard UI
11. Live synchronization
12. Avatar Adapter
13. Full E2E proofs
```

## 9. Completion boundary

The prior consolidation and production-readiness queue remain closed. This worklist is the new architectural completion path for the **Dashboard + Universal Aurora Context/Model Plane**.

Increment 11 must be treated as implementation of these explicitly defined contracts, with CI and live gates for every boundary. No unrelated feature work should be introduced into this sequence.
