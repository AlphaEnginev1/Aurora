# Current runtime and acceptance status — 2026-10-13

This is the current acceptance snapshot for engineering work on Aurora. Use it together with `docs/audits/CURRENT_ACCEPTANCE_AND_BRANCH_INVENTORY_2026-10-13.md`; older dated roadmap entries remain as historical evidence and must not override this snapshot.

## 1. Canonical architecture today

- **Canonical source:** GitHub branch `main`. At the start of this audit, HEAD was `1f88c28867ce15041665d81ec05815be9c132470` after PR #218.
- **Inference:** the existing provider-neutral `ModelRouter`, `ProviderRegistry`, and `ModelRegistry` remain canonical. The repository enforces `cost_policy=free_only`. NVIDIA is available through the official live Free Endpoint catalog when its credential is configured. OpenRouter is optional, requires `OPENROUTER_API_KEY`, and the reviewed model is only `openrouter/free`.
- **Director selection:** the normal mode is the canonical inference router/fabric. Direct OpenRouter selection is opt-in through local configuration/process environment; it must not be inferred merely from a successful models-list request.
- **OpenRouter quota safety:** PR #217 made the live workflow manual-only. A key-quota probe on PR #216's current test branch saw `used=51, limit=50, remaining=0` and stopped before generation. At this snapshot, a follow-up PR #220 is adding that fail-closed quota preflight to `main` itself; until #220 merges, do not manually run OpenRouter live inference on main while quota may be exhausted.
- **No paid fallback:** quota or provider failure is not authorization to choose paid models. Do not bypass the free-only model allowlist.

## 2. Point 2 — autonomous controller integration

**Closed for deterministic canonical-runtime integration.** PR #218 merged `tests/e2e/test_autonomy_runtime_bindings_acceptance.py`, which builds the real `build_director_runtime()` factory and exercises computer use, software engineering, discovery loop, and RSI in one acceptance path. It asserts shared canonical Workbench/Router/Director/Research/Learning identities and checks RSI fails closed without explicit CI/change evidence.

Post-merge CI run [38449077652](https://github.com/AlphaEnginev1/Aurora/actions/runs/38449077652) passed both `test` and `e2e-network`. The test uses deterministic mocks at external side-effect boundaries; it does not certify live model inference, unmocked research, physical workstation use, or the user's WSL runtime.

The narrower duplicate PR #196 was closed as superseded by #218; its branch has not been deleted.

## 3. Point 3 — branch hygiene

The current audit snapshot enumerated 232 branch refs including `main`. All 231 non-main refs were compared to main for ancestry/divergence: 144 heads had zero commits ahead and 87 had one or more commits ahead. These are ancestry classes only. A divergent ref can already have its code represented on main via squash merge.

**No branch was deleted or rewritten.** Open PRs requiring content-level disposition are #216 (provider/settings and NVIDIA catalog; OpenRouter quota gate pending), #192 (uploaded ZIP/PATCH artifacts must be preserved), #185 (one workflow-input change requiring current-branch review), and #183 (broad multi-provider branch; do not merge wholesale against the current provider policy). See the branch audit for the expanded queue.

## 4. Point 4 — protecting main

The branch-list API currently reports `protected: false`; the readable repository-rulesets endpoint was empty. The connected GitHub integration receives HTTP 403 reading classic branch-protection settings and has no write operation for protection/rulesets, so it could not enable or fully verify enforcement.

**Required admin action in GitHub UI:** open **Settings → Branches** (or **Settings → Rules → Rulesets**), configure a rule targeting exactly `main`, require pull requests before merging, require the canonical CI checks `test` and `e2e-network`, and enable the intended enforcement. Then re-open the rule and verify it actually applies to `main`. Do not infer protection from green CI alone.

## 5. Point 5 — documentation alignment

PR #219 reconciles the leading status in `ROADMAP.md` and the current rows in `docs/AURORA_CONSOLIDATION_MATRIX.md`, preserving old acceptance details as historical records. The specific current provider status remains separate from historical Increment 12/Gemini-to-NVIDIA proof.

## 6. Point 6 — clean local WSL start and a real conversation

GitHub CI cannot prove the state of the user's local WSL installation. This remains pending until tested on the actual workstation. From the Aurora WSL shell, after checking for local uncommitted work, use:

```bash
cd ~/AURORA
git status --short
git fetch origin
git switch main
git pull --ff-only origin main
bash scripts/restart-local.sh
curl -fsS --max-time 3 http://127.0.0.1:8787/ >/dev/null
```

Then use the dashboard to send one harmless text prompt to Director and confirm a non-empty reply plus its provider/model metadata. Do not print the environment or local credential file. Record the canonical commit, response provider/model, success/failure reason and timestamp; redact request content that contains private project information.

A dashboard HTTP 200 by itself is only a boot check, not evidence of a successful Director conversation.

## 7. Final provider/resilience acceptance

Still pending on the current canonical main SHA:
- run the dedicated NVIDIA Free Endpoint live gate against current main after its catalog/test changes; PR #216 reports two successful NVIDIA inference runs on its PR head, which is useful but not identical to a current-main run;
- wait until OpenRouter's account quota resets and the fail-closed preflight fix (#220) is merged and green before an explicitly manual free-inference acceptance run;
- execute the current multi-provider/reserve/fail-closed live gate with evidence showing primary failure, eligible NVIDIA reserve success, and durable mission/session continuity. If the primary is unavailable, confirm the controller fails closed rather than switching to any non-free provider;
- complete the local WSL conversation/clean restart in point 6 and record that evidence.

## Closure rule

A point is closed only against its explicit acceptance criteria and the current canonical code. Deterministic CI, historical workflow success, a successful authenticated model-list request, and a local dashboard boot each prove different things and are not interchangeable.