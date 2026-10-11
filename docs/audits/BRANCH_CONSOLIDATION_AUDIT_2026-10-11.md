# Aurora branch consolidation audit — 2026-10-11

## Scope and safety

Read-only inventory of branch refs and compare results against `main`. No branches deleted, force-pushed, merged, or rewritten. Branch-protection API returned HTTP 403 for this integration, so protection status is **unverified** (the branch listing reports `protected: false` for listed refs, which is not enough to conclude effective rulesets are absent).

## Repository snapshot

- Default branch: `main`
- Main HEAD at audit time: `275ffb37730ae2281b21df80d4e01a6f97888359`
- Branch refs enumerated: **209**, including `main` (the earlier estimate of 192 is stale).
- Open PRs observed: #196, #192, #185, #183.

## Verified comparisons against main

| Branch | Compare status | Ahead | Behind | Preliminary classification |
|---|---:|---:|---:|---|
| `fix/autonomy-runtime-e2e-wiring` | ahead | 1 | 0 | Open PR #196; retain pending CI/review |
| `review/claude-fase1-tarea2` | diverged | 3 | 61 | Preserve unique ZIP/PATCH artifacts; reconcile separately |
| `review/opus-fase1-tarea2` | diverged | 1 | 61 | Preserve unique ZIP/PATCH artifacts; reconcile separately |
| `fix/live-free-api-model-override` | diverged | 1 | 66 | Open PR #185; inspect one unique commit before merge |
| `feat/free-multi-provider-inference` | diverged | 39 | 68 | Open PR #183; high-risk reconciliation, not a blind merge |
| `cleanup/remove-duplicate-nvidia-acceptance` | diverged | 1 | 1 | Compare against current main; likely superseded but verify |
| `audit/nvidia-regression-20261008` | behind | 0 | 88 | Head is already in main history; stale-ref candidate, verify PR/artifacts first |
| `fix/fase0-corrected-archive-validation` | diverged | 2 | 63 | Preserve its unique workflow changes until reconciled |
| `fix/increment-12-live-nvidia-capability` | behind | 0 | 525 | Head is in main history; stale-ref candidate |
| `increment-12/director-provider-failover` | behind | 0 | 529 | Head is in main history; stale-ref candidate |
| `fix/dynamic-nvidia-model-failover` | behind | 0 | 309 | Head is in main history; stale-ref candidate |
| `fix/live-nvidia-discovery-activation` | diverged | 1 | 75 | Preserve unique commit; reconcile against current NVIDIA-only path |
| `fix/startup-dynamic-free-model-activation` | behind | 0 | 147 | Head is in main history; stale-ref candidate |
| `feat/dynamic-nvidia-free-catalog-20261006` | diverged | 12 | 174 | Preserve unique commits; reconcile before any cleanup |
| `feat/provider-availability-multi-provider` | behind | 0 | 235 | Head is in main history; stale-ref candidate |
| `feature/unified-provider-availability` | behind | 0 | 137 | Head is in main history; stale-ref candidate |
| `feature/multi-provider-openai-anthropic` | behind | 0 | 128 | Head is in main history; stale-ref candidate; current product policy is NVIDIA-only |
| `feature/dynamic-reserve-pool` | behind | 0 | 121 | Head is in main history; stale-ref candidate |
| `feature/complete-multi-provider-failure-e2e` | behind | 0 | 105 | Head is in main history; stale-ref candidate |
| `fix/nvidia-director-chat-failover` | behind | 0 | 155 | Head is in main history; stale-ref candidate |

## Diff evidence that affects disposition

- `feat/free-multi-provider-inference` has 39 commits not in main and 68 commits of main not in the branch. Its diff touches provider settings, OpenAI adapter, provider registry, reserve pool, router, factory, inference/free-model policies, and tests. It must not be merged as-is because it reintroduces OpenAI/multi-provider behavior into a currently NVIDIA-only configuration; useful generic changes must be cherry-picked/reimplemented selectively after review.
- `review/claude-fase1-tarea2` adds `Aurora-main-fase1-tarea2.zip`, `Aurora-main-fase1-tarea3.zip`, `files.zip`, and two patch files. Do not delete until their unique changes have been extracted and compared.
- `review/opus-fase1-tarea2` adds a ZIP and patch; preserve until compared with the Claude artifact and main.
- `fix/fase0-corrected-archive-validation` has a unique change to `.github/workflows/validate-fase0-archive.yml`; preserve and review the exact workflow diff before disposition.
- Branch names containing old NVIDIA fixes are not proof that their commits are lost: several heads are ancestors of current main (compare `ahead=0`). Do not reapply them without checking the resulting main tree.

## Open PRs

- #196 — `test: verify canonical autonomy controller runtime wiring`; branch head is one commit ahead of main. Keep pending its required checks/review.
- #192 — `Add files via upload`; branch `review/claude-fase1-tarea2`. Artifact-only changes need review before closing or merging.
- #185 — `ci: allow selecting an allowlisted model in live provider tests`; one unique commit on its branch, but branch is substantially behind main.
- #183 — `feat: add free multi-provider inference candidates`; substantial divergence and conflicts with NVIDIA-only policy unless narrowed.

## Next safe actions

1. Finish comparison for all 209 refs and classify each as: merged/stale candidate, open-PR branch, diverged with unique code, or artifact-only.
2. For each diverged branch, record unique commits and changed paths; inspect semantic diffs and test evidence.
3. Reconcile useful code into small topic branches based on current main; never merge whole stale branches blindly.
4. Only then propose deleting redundant refs, and only after confirming there are no open PRs or unique artifacts.
5. Retry branch-protection/ruleset verification with an authorized GitHub permission; current result is inconclusive.
