# Aurora Consolidation Matrix

**Audit status: FINAL CONSOLIDATION RECORD — FEATURE FREEZE**

This document is the authoritative implementation/status inventory produced by the consolidation gate.
It separates implementation existence, automated testing, runtime wiring, live-provider verification, and
genuine remaining work.

## 0. Canonical source of truth

- **Canonical engineering branch: `main`**
- Canonical commit after reconciliation: `e4dff9e0e15198d41cb5f3e086903fecd4e9d9f6`
- Canonical pre-test implementation baseline: `64193c0bbaccc0920c87f20054c83bc0c482d556`
- Consolidation evidence branch: `runtime/consolidation-gate`
- Pre-consolidation main preserved as: `archive/main-pre-consolidation-20260929`
- CI 1287: SUCCESS
- CI 1288: SUCCESS
- CI 1289: SUCCESS
- CI 1291: SUCCESS
- CI 1292: SUCCESS
- CI 1293: SUCCESS
- CI 1294: SUCCESS
- CI 1295: SUCCESS
- CI 1296: SUCCESS
- Consolidation issue: #50

**Decision:** `main` is now the single permanent engineering source of truth. The consolidation branch
remains as an auditable snapshot of the gate work and is not a second implementation line.

## 1. Definitive capability status

| Area | IMPLEMENTADO | TESTEADO | RUNTIME INTEGRADO | LIVE VERIFICADO | PENDIENTE REAL |
|---|---|---|---|---|---|
| Foundation / governance / security / CI | YES | YES | YES | N/A | Production hardening only |
| Capability Broker / Registry | YES | YES | YES | N/A | None identified |
| Autonomous capability discovery / acquisition | YES | YES | YES | NO | Production-source expansion (APT/PyPI path limits) |
| Acquisition transactions / provenance / dependency closure | YES | YES | YES | NO | Production drills |
| Sandbox / deterministic workspaces | YES | YES | YES | NO | Host-level enforcement verification |
| Application network gateway / proxy / dataplane | YES | YES | YES | NO | OS-level transparent enforcement decision + proof |
| Inference Fabric / ModelRouter | YES | YES | YES | NO | Live provider verification |
| NVIDIA provider | YES | YES | YES (provider contract) | NO | Live NVIDIA gate and production authorization |
| Director provider abstraction / Gemini adapter | YES | YES | YES (provider contract) | NO | Live Gemini gate and production authorization |
| Director 24/7 runtime | YES | YES | YES | N/A | Production observability / recovery drills |
| Loop safety / bounded mission review | YES | YES | YES | N/A | None identified |
| Organization / departments / agent selection | YES | YES | YES | N/A | None identified |
| Specialist skills foundation | YES | YES | YES | N/A | None identified |
| Skill evaluation / learning | YES | YES | YES | NO | Production live-provider evidence |
| Skill evolution / validation / promotion / rollback | YES | YES | YES | NO | Production live-provider evidence |
| Specialist acquisition / construction | YES | YES | YES | NO | Production live-provider evidence |
| Computer operator / credential boundary | YES | YES | YES | NO | Production authorization drills |
| Research domain / durable evidence | YES | YES | YES | NO | Live research-discovery runtime wiring |
| Research parsing / claim extraction | YES | YES | YES | NO | None identified |
| Research independent verification / contradiction handling | YES | YES | YES | NO | Production live-source drill |
| Trusted knowledge / citation graph | YES | YES | YES | NO | None identified |
| Research delegation to TaskEngine | YES | YES | YES | NO | None identified |
| Research → learning / routing trust gate | YES | YES | YES | NO | Live-provider verification |
| **Research HttpSearchProvider** | **YES** | **YES** | **NO — factory not wired** | NO | Wire governed provider into runtime factory/configuration |
| Director decision learning / comparative evidence | YES | YES | YES | N/A | None identified |
| Empirical decision knowledge promotion | YES | YES | YES | N/A | None identified |
| Director policy evolution / canary / rollback | YES | YES | YES | N/A | Production recovery drill |
| Active typed policy contract | YES | YES | YES | N/A | None identified |
| Live provider workflows | YES (workflow code) | YES (workflow definition) | OPT-IN ONLY | **NO** | Execute and record real NVIDIA/Gemini runs |

### Meaning of the five columns

- **IMPLEMENTADO:** code path exists in the canonical source.
- **TESTEADO:** automated tests provide evidence on the canonical line.
- **RUNTIME INTEGRADO:** the normal Aurora runtime factory/orchestration path wires and can invoke it.
- **LIVE VERIFICADO:** a real external provider/environment run has been executed and its evidence recorded.
- **PENDIENTE REAL:** work that is still genuinely required; this is not a synonym for “nice to have”.

## 2. Research discovery finding

The canonical line contains `HttpSearchProvider` and `ResearchService.discover()`.
The provider boundary now has explicit tests covering:

1. unique URL discovery and request construction;
2. fail-closed behavior when no discovery provider is configured;
3. successful `ResearchService` use of an explicitly supplied discovery provider.

However, `build_director_runtime()` currently constructs:

`ResearchService(gateway, ResearchStore(...))`

without supplying a `discovery=HttpSearchProvider(...)` instance.

Therefore the precise status is:

**IMPLEMENTADO = YES**  
**TESTEADO = YES**  
**RUNTIME INTEGRADO = NO**  
**LIVE VERIFICADO = NO**  
**PENDIENTE REAL = governed runtime wiring + configuration + live evidence**

No autonomous research-discovery feature should be reimplemented; only the missing runtime connection remains.

## 3. Live provider verification

Two dedicated manual/opt-in workflows exist:

- `.github/workflows/live-nvidia-learning.yml`
- `.github/workflows/live-gemini-director.yml`

Their existence is not evidence of live operation. The consolidation audit found no successful live-provider run
that can be promoted to **LIVE VERIFICADO**.

Therefore:

- NVIDIA: IMPLEMENTADO / TESTEADO / RUNTIME INTEGRADO at provider-contract level / LIVE VERIFICADO = NO.
- Gemini: IMPLEMENTADO / TESTEADO / RUNTIME INTEGRADO at provider-contract level / LIVE VERIFICADO = NO.

The remaining action is execution and recording of the real gates under the appropriate authorization and secrets.

## 4. Network enforcement

Aurora has application-level network governance through the InternetGateway, egress proxy and network dataplane.
The remaining boundary is whether production requires **transparent OS-level enforcement** that cannot be bypassed by
a process escaping the application-level control plane.

This is now classified as a production-security decision and verification item, not an unimplemented duplicate
network architecture.

## 5. Observability and recovery

The runtime already contains durable task/event state, leases, reconciliation, bounded execution and restart
recovery paths. What remains for production is operational evidence:

- production-grade observability coverage;
- controlled crash/restart/recovery drills;
- evidence that the documented recovery guarantees hold under representative failures.

This is verification/hardening of existing runtime behavior, not a new orchestration feature.

## 6. Historical branch classification

At consolidation time the repository contains **57 branch refs**:

- 1 canonical branch: `main`;
- 1 consolidation evidence branch: `runtime/consolidation-gate`;
- 1 preserved pre-consolidation main snapshot: `archive/main-pre-consolidation-20260929`;
- 55 other historical branches.

All 55 historical branches are classified **ARCHIVE / INTEGRATED**. They are not future implementation lines and
are not new work queues.

### 6.1 Historical branches that are direct ancestors of the canonical line (35)

`fix/bwrap-status-framing-v1`  
`fix/network-securebits-test-v1`  
`foundation/aurora-architecture-v1`  
`integration/aurora-foundation`  
`runtime/acquisition-service-v2-e2e`  
`runtime/acquisition-service-v3-dependency-closure`  
`runtime/acquisition-transactions-v1`  
`runtime/adaptive-director-memory-loop`  
`runtime/adaptive-mission-teams`  
`runtime/agent-department-registry-routing`  
`runtime/agent-skills-specialization`  
`runtime/capability-broker-execution-e2e`  
`runtime/concurrency-hardening`  
`runtime/controlled-network-broker-v1`  
`runtime/director-eventbus-24x7`  
`runtime/director-task-engine`  
`runtime/durable-task-queue`  
`runtime/egress-proxy-enforcement-v1`  
`runtime/execution-sandbox-v1`  
`runtime/filesystem-staging-rollback-v1`  
`runtime/increment-9-provider-contract`  
`runtime/increment-10-director-contract`  
`runtime/inference-fabric-v1`  
`runtime/isolated-network-dataplane-v1`  
`runtime/knowledge-claims-extraction`  
`runtime/knowledge-research-intelligence`  
`runtime/package-provenance-v1`  
`runtime/research-learning-routing`  
`runtime/research-task-delegation`  
`runtime/skill-acquisition-evolution`  
`runtime/skill-evaluation-learning-dynamic-routing`  
`runtime/task-worker-24x7`  
`runtime/team-mission-runtime`  
`runtime/trusted-knowledge-artifacts`  
`runtime/verification-contradictions`

### 6.2 Historical branches that diverged from the canonical line but are still integrated (20)

These were inspected as divergent historical work. Their unique commits correspond to functionality already
represented in the canonical line or to older variants superseded by later hardening.

`runtime/adaptive-routing-proof`  
`runtime/autonomous-capability-engine`  
`runtime/autonomous-skill-evolution`  
`runtime/autonomous-specialist-acquisition`  
`runtime/crash-recovery-reconciliation-v1`  
`runtime/credential-identity-broker`  
`runtime/evidence-driven-skill-generation`  
`runtime/governed-computer-operator`  
`runtime/governed-web-discovery`  
`runtime/model-performance-integration-fix`  
`runtime/model-performance-learning`  
`runtime/richer-skill-evidence`  
`runtime/skill-acquisition-pipeline`  
`runtime/skill-discovery-construction`  
`runtime/skill-promotion-team-reformation`  
`runtime/skill-regression-rollback`  
`runtime/skill-validation-durability`  
`runtime/skill-validation-lab`  
`runtime/tool-artifact-injection`  
`runtime/validation-replay-context`

Notably, the old `runtime/governed-web-discovery` branch carries an earlier/simple ResearchService implementation;
the canonical line contains the later research parsing, provenance, verification and knowledge work. Its branch is
therefore historical and must not be used to reconstruct current behavior.

## 7. Main reconciliation decision

The old `main` was not used as the implementation source of truth because it was structurally behind the
integration line while also carrying six historical commits. Those commits were:

- governed runtime skill acquisition + its corrective revert;
- governed web discovery provider;
- live Gemini Director workflow exposure.

The actual implementation line already contains the corresponding runtime capabilities and a current Gemini
workflow. The old `main` state is preserved intact at:

`archive/main-pre-consolidation-20260929`

The canonical implementation is therefore the audited consolidation line, now promoted to:

**`main`**

## 8. CLOSED / DO NOT REOPEN

Do not reopen as new increments:

- foundation/governance/security;
- capability broker/registry;
- acquisition/provenance/transactions/dependency closure;
- sandbox/workspace isolation;
- application network gateway/proxy/dataplane;
- Director 24/7 runtime;
- bounded mission review/replan;
- organization/departments;
- specialist skills;
- skill evaluation/learning/evolution/rollback;
- specialist acquisition;
- computer/credential governance;
- research evidence/parsing/verification/knowledge;
- research delegation and trust-gated learning;
- adaptive routing proof;
- provider execution contract;
- Director decision learning/comparison/replication;
- empirical decision knowledge;
- policy evolution/canary/rollback;
- active policy registry and immutable active contract.

The consolidation matrix is the first check before proposing any future increment.

## 9. Genuine remaining production work

After consolidation, the finite remaining list is:

1. **Research discovery runtime wiring** — connect the existing `HttpSearchProvider` to the normal Director runtime
   through governed configuration, then test the complete path.
2. **OS-level network enforcement** — decide the required host boundary and execute an end-to-end proof if required.
3. **Live NVIDIA verification** — execute and record a real provider gate.
4. **Live Gemini verification** — execute and record a real provider gate.
5. **Production observability** — close remaining operational visibility gaps.
6. **Recovery drills** — execute controlled crash/restart/reconciliation exercises and archive evidence.
7. **Controlled production authorization** — define the explicit live-operation gate and evidence requirements.

No Increment 11 is authorized by this document. New functionality remains frozen until the consolidation gate
is explicitly closed and the next increment is derived from this finite evidence-backed list.
