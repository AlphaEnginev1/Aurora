# Free API provider setup

Aurora keeps the canonical `ModelRouter`, `ProviderRegistry`, and `ModelRegistry`. A provider is considered for inference only when its model is explicitly present in `config/free-model-policy.json`, its credential is configured, and any required free-tier confirmation is set.

## Never commit credentials

Set credentials in the local process environment or a secret manager. Do not put API keys in Git, `.env` files committed to the repository, logs, screenshots, or issue comments.

## Providers and eligible candidates

| Provider | Environment key | Initial allowlist | Activation gate |
|---|---|---|---|
| NVIDIA | `NVIDIA_API_KEY` | Official live NVIDIA Free Endpoint catalog | Live catalog eligibility |
| Groq | `GROQ_API_KEY` | `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `openai/gpt-oss-20b` | `AURORA_GROQ_FREE_TIER_CONFIRMED=1` |
| Google AI Studio | `GEMINI_API_KEY` | `gemini-3.8-flash` | `AURORA_GEMINI_FREE_TIER_CONFIRMED=1` |
| Cloudflare Workers AI | `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` | Llama 3.3 70B and Llama 3.1 8B FP8 Fast | `AURORA_CLOUDFLARE_FREE_TIER_CONFIRMED=1` |
| Mistral | `MISTRAL_API_KEY` | `labs-leanstral-2603` only | `AURORA_MISTRAL_FREE_TIER_CONFIRMED=1`; time-limited free availability must be rechecked |
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter/free` only | Free router endpoint only; no paid model IDs are allowed |

The confirmation flags are operator attestations, not automated account-plan detection. Set them only after confirming the account and key cannot incur charges for the configured route. If you cannot confirm this, leave the flag unset and Aurora will not activate that provider's models.

## Cost and privacy safeguards

- `AURORA_INFERENCE_COST_POLICY` must remain `free_only`. The canonical runtime refuses any other value.
- Unknown model IDs and model IDs absent from the explicit free allowlist are ineligible.
- Groq, Gemini, Cloudflare, and Mistral models are not seeded without their own key and the required free-tier confirmation.
- Cloudflare must remain on the Workers Free plan, with no paid plan or prepaid AI Gateway credits. The documented free allocation is 10,000 Neurons per day; the free plan rejects usage over that allocation. Account upgrades can change billing behavior, so recheck the account plan.
- Google documents that free-tier Gemini prompts may be used to improve Google products. Do not send confidential project data unless that use is acceptable.
- OpenRouter is pinned to `openrouter/free`; never replace it with an unqualified or paid model ID.
- A free tier can change, be exhausted, or be discontinued. On errors, Aurora should fail over only to another eligible free candidate; if none remains, it must fail closed.
- Routing quality/latency values in the policy are initial priors, not benchmark results. Update them using measured success, quality, latency, and failover evidence.

## Suggested local environment setup

Set only the providers whose account terms you have verified. Example for a WSL shell (replace the placeholders locally; never paste real values into GitHub):

```bash
export GROQ_API_KEY='...'
export AURORA_GROQ_FREE_TIER_CONFIRMED=1

export GEMINI_API_KEY='...'
export AURORA_GEMINI_FREE_TIER_CONFIRMED=1

export CLOUDFLARE_API_TOKEN='...'
export CLOUDFLARE_ACCOUNT_ID='...'
export AURORA_CLOUDFLARE_FREE_TIER_CONFIRMED=1

export MISTRAL_API_KEY='...'
export AURORA_MISTRAL_FREE_TIER_CONFIRMED=1

export OPENROUTER_API_KEY='...'
```

Do not set confirmation flags blindly. The app does not have an authoritative API to prove whether an account is on a free-only billing plan, so live inference validation must be run only after the operator has checked account settings.

## Official references checked 2026-10-09

- Groq model catalog and deprecations: https://console.groq.com/docs/models and https://console.groq.com/docs/deprecations
- Google Gemini API pricing and free-tier data-use terms: https://ai.google.dev/gemini-api/docs/pricing
- Cloudflare Workers AI pricing and free allocation: https://developers.cloudflare.com/workers-ai/platform/pricing/
- Mistral API pricing and free Labs model listing: https://mistral.ai/pricing/api/
- Mistral Studio free-mode quickstart: https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key
- OpenRouter free models: https://openrouter.ai/collections/free-models
