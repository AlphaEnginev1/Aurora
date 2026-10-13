# Auditoría de aceptación y ramas — 2026-10-13

## Re-audit refresh — 2026-10-13 (current at main baseline `a87e4bce7634afef0e3543b540d7cce49fec9799`)

This refresh supplements the older point-in-time sections below; it does not erase historical comparison data.

- **Current branch refs at refresh:** 240 including `main`, 239 non-main. The previous complete ancestry comparison covered 231 heads (144 zero-ahead, 87 ahead/diverged). Eight subsequent refs were compared against refreshed main and each has commits ahead: `audit/current-acceptance-and-branch-inventory-20261013`, `docs/reconcile-current-acceptance-20261013`, `fix/openrouter-main-quota-preflight-20261013`, `fix/nvidia-live-reserve-acceptance-20261013`, `fix/nvidia-free-catalog-parser-20261013`, `fix/provider-http-diagnostics-nvidia-openrouter-20261013`, `docs/final-seven-point-audit-20261013`, and `docs/finish-inventory-audit-20261013`. Updated ancestry tally: **144 zero-ahead, 95 ahead/diverged**. It remains an ancestry tally, not a complete semantic review.
- **Merged since the earlier snapshot:** #220 OpenRouter quota preflight; #221 manual-only controlled NVIDIA reserve drill; #222 roadmap/matrix reconciliation; #223 NVIDIA Free Endpoint parser hardening; #224 secret-safe NVIDIA/OpenRouter HTTP diagnostics.
- **Point 1:** code and CI for the manual NVIDIA primary + controlled primary failure + real different NVIDIA reserve response is ready. It has **not yet been manually run**. It does not prove natural primary-provider outage handling.
- **Point 2:** deterministic runtime integration closed by #218 and its post-merge CI.
- **Point 3:** no branch was deleted. Keep the archive branch and uploaded ZIP/PATCH artifacts; high-risk #183/#192/#216 remain open and require file-level review before any cleanup.
- **Point 4:** `main protected=false`; accessible rulesets were empty and classic protection read returned HTTP 403. Admin setup/verification is still required.
- **Point 5:** this re-audit refreshed `ROADMAP.md`, the matrix and the operational report; old entries below remain historical.
- **Point 6:** local WSL boot plus a real Director conversation remain unverified and must be run on the workstation.
- **Point 7:** OpenRouter generation still awaits a positive quota preflight; the last confirmed reading was `used=51, limit=50, remaining=0`. Both live provider workflows are manual-only. No paid fallback is enabled.

## Canonical snapshot

- Canonical branch: `main`
- HEAD at report creation: `1f88c28867ce15041665d81ec05815be9c132470`
- Point 2 joint autonomy acceptance merged by PR #218.
- Main CI after the merge: `test` and `e2e-network` SUCCESS (run [38449077652](https://github.com/AlphaEnginev1/Aurora/actions/runs/38449077652)).
- OpenRouter's live inference workflow is manual-only since PR #217. It must not be triggered as a side effect of routine pull-request updates.

## Point 2 — four autonomy controllers

The canonical runtime factory exposes computer use, software engineering, discovery loop, and RSI via `build_autonomy_runtime_bindings()`. PR #218 adds a joint deterministic E2E that builds the production runtime, checks identity of the canonical Workbench/Router/Director/Research/Learning services, and invokes all four controllers together with side-effect seams stubbed.

The test proves deterministic composition and the RSI fail-closed rule. It is not live NVIDIA/OpenRouter evidence, does not exercise physical workstation I/O, and does not prove that an unmocked external research endpoint is configured.

**Status: deterministic controller integration accepted; live/WSL acceptance is a separate outstanding gate.**

## Current branch inventory

The live branch endpoint returned **232 refs including `main`**, i.e. **231 non-main branch refs**, at the snapshot time. This enumeration predates branches subsequently created for the current audit/documentation and the quota-preflight fix; refresh the live count before any final cleanup. Every non-main head present in that snapshot was compared with `main` for ancestry/divergence. The comparison batch ran against main SHA `989b58db91440ebdc5b782bfb8a32980ba1040c1`, immediately before PR #218 was squash-merged. That merge advanced main by one commit; it does not change the high-level classification, but exact behind counts should be refreshed for any final cleanup decision.

- **144 heads had zero commits ahead of main** in the compare snapshot. These are ancestry/stale-reference candidates, not automatic deletion targets. This set includes intentional historical/archive refs; keep `archive/main-pre-consolidation-20260929`.
- **87 heads had one or more commits ahead of main** (including diverged/squash-merged candidates). This means the ref has unique commit ancestry, not necessarily unique product functionality: squash-merged PRs commonly keep a different commit SHA while carrying code already present in main.
- No branch was deleted, rewritten, or force-pushed as part of this audit.
- The earlier audit `docs/audits/BRANCH_CONSOLIDATION_AUDIT_2026-10-11.md` enumerated 209 refs and explicitly described its compare table as partial. Its historical count is stale.

### High-priority branches requiring content-level review

| Branch / PR | Current finding | Safe disposition |
|---|---|---|
| `feature/provider-credentials-settings` / #216 | Large 76-commit provider-settings/NVIDIA catalog changes; PR remains open. Branch is not the same as current main. The OpenRouter quota probe reports 51 used / 50 allowed and prevents another chat generation. | Keep open; do not merge blindly or run OpenRouter live inference until allowance returns. |
| `review/claude-fase1-tarea2` / #192 | Artifact/upload PR containing ZIP/PATCH material. | Preserve archives and inspect differences before closing or merging. |
| `review/opus-fase1-tarea2` | Divergent branch with ZIP/PATCH material; no deletion performed. | Preserve pending content comparison against Claude artifacts and main. |
| `fix/live-free-api-model-override` / #185 | Closed unmerged on 2026-10-13; branch retained. Its `model_id` input targeted a superseded multi-provider workflow and does not fit current `openrouter/free`-only main. | Do not merge; reconsider only with a revised, approved workflow contract. |
| `feat/free-multi-provider-inference` / #183 | 39 commits ahead in the compare snapshot, 15 changed files; provider/registry/policy changes. | Do not merge the branch wholesale: review selectively against the current NVIDIA-first, free-only contract. |
| `feat/openrouter-canonical-main-20261012` / #200 | Divergent ancestry despite PR #200 already merged. | Treat as a squash-merge residue; do not merge/reapply changes blindly. |
| `fix/openrouter-egress-policy-20261012` / #201 | Divergent ancestry although PR #201 already merged. | Treat as squash-merge residue; preserve branch until cleanup policy is approved. |
| `fix/autonomy-runtime-e2e-wiring` / #196 | Narrow wiring-only test PR closed as superseded by stronger merged PR #218. | No branch deletion; content still recorded in history. |
| `test/autonomy-controller-joint-e2e-20261013` / #218 | Acceptance PR is merged to main. Its branch remains. | A future cleanup may remove the redundant ref only after checking repo retention policy and PR/artifact references. |

### Next safe steps for branch cleanup

1. For each of the 87 heads with commits ahead of main, compare file-level changes and open PR/artifact references; separate genuine unique code from squash-merge residue.
2. Review open PRs #183, #192 and #216 against current main; do not merge broad divergent branches as a cleanup shortcut. PR #185 is closed unmerged; its branch is retained.
3. Verify uploaded ZIP/PATCH artifacts have been extracted/compared before considering any branch removal.
4. Only after that review, propose a deletion list separately. This audit does **not** authorize deleting branches.

## Main branch protection

The branches API currently reports `protected: false` for `main`, and the accessible repository-rulesets endpoint returned an empty list. The classic protection endpoint returned HTTP 403 to the connected integration, so its full configuration could not be read. **Effective enforcement remains unverified and is an unresolved acceptance item**; do not represent CI success as proof that main is protected.

This connector has read-only GitHub access for rulesets/branch protection; no settings mutation was performed.

## Provider and local-runtime boundaries

- The OpenRouter live workflow is now manual-only. A successful model-list request is connection evidence, not inference evidence.
- The live quota preflight found `used=51, limit=50, remaining=0` for the GitHub Actions key on run [38427655156](https://github.com/AlphaEnginev1/Aurora/actions/runs/38427655156); that run sent no chat completion after seeing zero remaining requests.
- The current PR #216 reports two successful live NVIDIA inference runs on its PR head. Those results do not equal a live acceptance run on the post-PR-218 main SHA.
- A clean WSL restart and real Director conversation are not established by GitHub-hosted CI. They still require evidence from the user's WSL machine.

## Acceptance status summary

| Item | Status at report time |
|---|---|
| 2. Four autonomy controllers in canonical runtime + joint deterministic E2E | **CLOSED** by PR #218; CI green |
| 3. Complete branch content classification and safe cleanup | **IN PROGRESS**; ancestry comparison across all refs done, 87 divergent heads still need semantic/file-level review |
| 4. Effective protection of main | **UNVERIFIED**; requires authorized GitHub Settings/admin read and potentially a policy change |
| 5. Current architecture/status documentation reconciled | **THIS REPORT PARTIALLY UPDATES IT**; roadmap/consolidation matrix still require a focused update on current provider/live status |
| 6. Clean local WSL startup + actual conversation | **PENDING**; GitHub CI cannot prove the user's local runtime |
| 7. Current live provider/resilience and final end-to-end acceptance | **PENDING**; no fresh OpenRouter inference while quota is exhausted, and post-merge main needs provider/WSL acceptance evidence |

## Safety

No branch was deleted, no history was rewritten, no paid provider route was enabled, and no extra OpenRouter generation request was sent.
