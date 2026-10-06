# Live Free-Model Resilience Gate

**Status:** GATE DEFINED — AWAITING LIVE EXECUTION  
**Canonical implementation:** `main`

This gate closes the LIVE evidence gap for the current inference-resilience architecture without introducing paid provider dependencies. It does not reopen Increment 11, Increment 12 or the frozen production-readiness queue.

## Scope

The gate exercises the canonical implementation using only free-model access:

1. Gemini free tier as the current Director primary.
2. NVIDIA NIM free endpoints for the autonomous specialist/reserve pool.
3. Durable `ProviderHealthStore` success/failure state.
4. Current NVIDIA catalog discovery with a fail-closed free-model allowlist.
5. Dynamic Director reserve-pool construction.
6. Real Gemini → NVIDIA Director failover under a controlled primary failure.
7. Critical-decision `ModelEnsemble` across distinct free NVIDIA models.
8. `DirectorRuntime` multi-agent adversarial review across the same free-only router.
9. Durable failover, health, decision and review evidence.

The test never requires OpenAI or Anthropic credentials and the workflow explicitly rejects those variables.

## Free-model policy

The checked-in `config/free-model-policy.json` is the source of truth for models Aurora is allowed to route in `free_only` mode.

Current curated models:

- Gemini: `gemini-3.8-flash`, `gemini-3.7-flash`
- NVIDIA NIM: `nvidia/nemotron-3-ultra-550b-a55b`
- NVIDIA NIM: `nvidia/nemotron-3-super-120b-a12b`
- NVIDIA NIM: `z-ai/glm-5-3`
- NVIDIA NIM: `z-ai/glm-5-3-flash`
- NVIDIA NIM: `deepseek-ai/deepseek-v4.1-flash`
- NVIDIA NIM: `google/gemma-4-31b-it`
- NVIDIA NIM: `poolside/laguna-xs-2.1`

Google currently documents Gemini 3.8 Flash as Free Tier. NVIDIA currently exposes the listed NIM model pages with Free Endpoint availability. The runtime treats the allowlist as authoritative and stages newly discovered models without activating them automatically.

## GitHub environment

Environment: `multi-provider-live`

Required secrets:
- `GEMINI_API_KEY`
- `NVIDIA_API_KEY`

No OpenAI or Anthropic secrets are required or used.

Workflow inputs may override the Gemini/NVIDIA model IDs, but an override is accepted only when the selected model is explicitly present in the free-model policy.

## Acceptance

A green run is the LIVE acceptance evidence for the current free-model resilience implementation.

The gate fails closed when:
- credentials are missing;
- the configured Director model is not policy-authorized as free;
- fewer than three distinct free NVIDIA reasoning candidates are discovered;
- a real free-model inference fails;
- the Director failover does not reach NVIDIA;
- the critical-decision ensemble lacks sufficient successful independent members;
- adversarial reviewers cannot execute through the free-only router.

The gate does not authorize production execution. Controlled Live Authorization remains the activation boundary.

## Evidence already closed

LIVE-RESEARCH-DISCOVERY, LIVE-NVIDIA, LIVE-GEMINI, Increment 12 provider failover, Universal Workbench acceptance, production observability and recovery remain independently verified.
