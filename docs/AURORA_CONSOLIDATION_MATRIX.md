# Aurora Consolidation Matrix

**Consolidation status: COMPLETE — RECONCILED WITH CURRENT MAIN**

This document is the authoritative implementation/status inventory produced by the consolidation gate.
It separates implementation existence, automated testing, runtime wiring, live-provider verification, and
genuine remaining work.

## 0. Canonical source of truth

- **Canonical engineering branch: `main`**
- Canonical pre-consolidation implementation baseline: `64193c0bbaccc0920c87f20054c83bc0c482d556`
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
- Consolidation issue: #50 (CLOSED)
- CI 1305: SUCCESS — research discovery E2E on verification branch
- CI 1306: SUCCESS — merged canonical `main`

**Decision:** `main` is now the single permanent engineering source of truth. The consolidation branch
remains as an auditable snapshot of the gate work and is not a second implementation line.

## 1. Definitive capability status

| Area | IMPLEMENTADO | TESTEADO | RUNTIME INTEGRADO | LIVE VERIFICADO | PENDIENTE REAL |
|---|---|---|---|---|---|
| Foundation / governance / security / CI | YES | YES | YES | N/A | Production hardening only |
| Capability Broker / Registry | YES | YES | YES | N/A | None identified |
| Autonomous capability discovery / acquisition | YES | YES | YES | YES | Production-source expansion (APT/PyPI path limits) |
| Acquisition transactions / provenance / dependency closure | YES | YES | YES | YES | None identified in frozen queue |
| Sandbox / deterministic workspaces | YES | YES | YES | YES | None identified in frozen queue |
| Application network gateway / proxy / dataplane | YES | YES | YES | YES | None identified in frozen queue |
| Inference Fabric / ModelRouter | YES | YES | YES | YES | None identified in frozen queue |
| Provider Health / Availability Layer | YES | YES | YES | PENDING NEW LIVE GATE | LIVE multi-provider acceptance |
| OpenAI provider + discovery adapter | YES | YES | YES | PENDING NEW LIVE GATE | LIVE provider contract |
| Anthropic provider + discovery adapter | YES | YES | YES | PENDING NEW LIVE GATE | LIVE provider contract |
| Dynamic multi-provider Director reserve pool | YES | YES | YES | PENDING NEW LIVE GATE | LIVE reserve/failover acceptance |
| Controlled autonomous model-catalog refresh | YES | YES | YES | PENDING NEW LIVE GATE | LIVE discovery/refresh acceptance |
| Critical Decision Model Ensemble | YES | YES | YES | PENDING NEW LIVE GATE | LIVE ensemble acceptance |
| Multi-Agent Adversarial Review | YES | YES | YES | PENDING NEW LIVE GATE | LIVE adversarial-review acceptance |
| NVIDIA provider | YES | YES | YES | YES | None identified in frozen queue |
| Director provider abstraction / Gemini adapter | YES | YES | YES | YES | None identified in frozen queue |
| Director 24/7 runtime | YES | YES | YES | N/A | None identified in frozen queue |
| Loop safety / bounded mission review | YES | YES | YES | N/A | None identified |
| Organization / departments / agent selection | YES | YES | YES | N/A | None identified |
| Specialist skills foundation | YES | YES | YES | N/A | None identified |
| Skill evaluation / learning | YES | YES | YES | YES | None identified in frozen queue |
| Skill evolution / validation / promotion / rollback | YES | YES | YES | YES | None identified in frozen queue |
| Specialist acquisition / construction | YES | YES | YES | YES | None identified in frozen queue |
| Computer operator / credential boundary | YES | YES | YES | YES | None identified in frozen queue |
| Research domain / durable evidence | YES | YES | YES | YES | None identified in frozen queue |
| Research parsing / claim extraction | YES | YES | YES | YES | None identified |
| Research independent verification / contradiction handling | YES | YES | YES | YES | None identified in frozen queue |
| Trusted knowledge / citation graph | YES | YES | YES | YES | None identified |
| Research delegation to TaskEngine | YES | YES | YES | YES | None identified |
| Research → learning / routing trust gate | YES | YES | YES | YES | None identified in frozen queue |
| **Research HttpSearchProvider** | **YES** | **YES** | **YES** | **YES** | None identified in frozen queue |
| Director decision learning / comparative evidence | YES | YES | YES | N/A | None identified |
| Empirical decision knowledge promotion | YES | YES | YES | N/A | None identified |
| Director policy evolution / canary / rollback | YES | YES | YES | N/A | None identified in frozen queue |
| Active typed policy contract | YES | YES | YES | N/A | None identified |
| Live provider workflows | YES (workflow code) | YES (workflow definition) | OPT-IN ONLY | **YES** | Dedicated provider gates verified; new multi-provider gate pending execution |

### Meaning of the five columns

- **IMPLEMENTADO:** code path exists in the canonical source.
- **TESTEADO:** automated tests provide evidence on the canonical line.
- **RUNTIME INTEGRADO:** the normal Aurora runtime factory/orchestration path wires and can invoke it.
- **LIVE VERIFICADO:** a real external provider/environment run has been executed and its evidence recorded.
- **PENDIENTE REAL:** work that is still genuinely required; this is not a synonym for “nice to have”.

## Production-readiness closure

The frozen production-readiness queue 1–7 is complete on the canonical `main` line.

Verified blocks:
1. LIVE-RESEARCH-DISCOVERY — VERIFIED
2. OS-NETWORK-ENFORCEMENT — VERIFIED
3. LIVE-NVIDIA — VERIFIED
4. LIVE-GEMINI — VERIFIED
5. PRODUCTION-OBSERVABILITY — VERIFIED
6. RECOVERY-DRILLS — VERIFIED
7. CONTROLLED-LIVE-AUTHORIZATION — VERIFIED

Block 7 establishes the authorization boundary; it does not itself grant active live operation. Live execution remains denied unless a human operator supplies a valid explicit authorization record matching the verified canonical commit and prerequisite state.

## 2. Research discovery finding

The canonical line contains `HttpSearchProvider` and `ResearchService.discover()`. `build_director_runtime()` now wires an explicitly configured `AURORA_RESEARCH_DISCOVERY_ENDPOINT` through `InternetGateway` into `HttpSearchProvider` and uses `ResearchService.discover()` as the runtime source-discovery callback for specialist research.

The end-to-end verification covers:

1. unique URL discovery and request construction;
2. fail-closed behavior when no discovery provider is configured;
3. successful `ResearchService` use of an explicitly supplied discovery provider.

Therefore the canonical production-readiness status is:

**IMPLEMENTADO = YES**  
**TESTEADO = YES**  
**RUNTIME INTEGRADO = YES**  
**LIVE VERIFICADO = YES**  
**PENDIENTE REAL = none in the frozen production-readiness queue**

The dedicated LIVE-RESEARCH-DISCOVERY gate sequence is closed and verified. The historical controlled/local HTTP E2E remains valid as component evidence; the dedicated live gate provides the production-readiness closure.

## 3. Live provider verification

The dedicated live-provider gates have been executed successfully and are closed:

- LIVE-NVIDIA — verified by the dedicated Live NVIDIA Learning Gate (#11).
- LIVE-GEMINI — verified by the dedicated Gemini Director Gate (#6, plus subsequent CI hardening and fail-closed checks).
- LIVE-RESEARCH-DISCOVERY — verified by the dedicated research discovery gate sequence through the final green gate (#27).

These runs prove the intended provider/runtime integration paths. They do not create an automatic production authorization.

## 3.1 Director provider failover / replacement

Increment 12 is externally verified on canonical `main`.

- deterministic provider failover and durable continuity tests: VERIFIED;
- combined LIVE gate: VERIFIED on GitHub Actions run `37179243481`;
- real Gemini primary success: VERIFIED;
- controlled Gemini failure after the primary response: VERIFIED;
- real NVIDIA replacement response: VERIFIED;
- durable failover evidence with no credential leakage in workflow output: VERIFIED;
- automatic switchback is not performed;
- explicit live authorization remains a separate human-controlled boundary.

## 3.2 Current multi-provider resilience closure

PRs #149, #150 and #151 are integrated on canonical `main`.

### PR #149 — Critical Decision Model Ensemble

The critical-decision ensemble is implemented, tested, runtime-integrated and deterministically verified. It uses the existing `ModelRouter`, dynamically selects distinct eligible models, persists member evidence, supports a dynamically routed resolver and fails closed when the required evidence cannot be obtained.

**LIVE status: PENDING NEW LIVE GATE.**

### PR #150 — Multi-Agent Adversarial Review

The Director now selects a governed multi-agent review panel and injects read-only independent findings into the existing Director review path. Findings are durable and advisory; the Director remains the final decision authority.

**LIVE status: PENDING NEW LIVE GATE.**

### PR #151 — Provider Availability & Multi-Provider Resilience

The canonical implementation now includes:

- durable provider/model availability and cooldown/probing;
- health-aware routing and execution feedback;
- OpenAI and Anthropic adapters plus model discovery;
- dynamic reserve-pool construction using the existing provider failover controller;
- rate-limited controlled model-catalog refresh;
- deterministic multi-provider outage/fail-closed coverage.

Automated CI is green on the merged canonical commit.

**LIVE status: PENDING NEW LIVE GATE.**

The dedicated acceptance workflow is:

`.github/workflows/live-multi-provider-resilience.yml`

The acceptance contract and evidence record are:

`docs/operations/LIVE_MULTI_PROVIDER_RESILIENCE.md`

This gate must complete successfully before these new capabilities are marked `LIVE VERIFICADO`. It does not alter the separate Controlled Live Authorization boundary.

## 4. Network enforcement

The OS-level network enforcement block is verified. Aurora's application gateway/proxy/dataplane remains integrated with the fail-closed sandbox enforcement boundary, and the dedicated enforcement evidence has been closed in the frozen production-readiness queue.

## 5. Observability and recovery

Production observability and controlled recovery drills are both verified. The implementation observes the existing durable Director/task lifecycle without changing authoritative decisions, and the recovery drills validate worker crash, stale ownership, Director restart, dependency recovery, and durable event replay behavior.

## 6. Historical branch classification

At consolidation time the repository contained **58 branch refs**. This historical count is intentionally frozen; later feature branches are not part of the consolidation inventory.

- 1 canonical branch: `main`;
- 1 consolidation evidence branch: `runtime/consolidation-gate`;
- 1 preserved pre-consolidation main snapshot: `archive/main-pre-consolidation-20260929`;

The pre-consolidation `main` state remains untouched in that archive branch for historical evidence.
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

## 9. Production-readiness queue status

The frozen production-readiness queue 1–7 is **COMPLETE / VERIFIED** on canonical `main`.

There is no remaining block in this queue. Historical branches and closed PRs must not be used as alternate implementation queues.

The newer Increment 13 architecture is tracked separately from that frozen queue and is not considered LIVE VERIFIED until `.github/workflows/live-multi-provider-resilience.yml` completes successfully.

The Controlled Live Authorization gate is established, but **live execution is not active by default**. A human operator must explicitly provide a valid authorization artifact; no authorization record or provider credential is stored in the repository.

