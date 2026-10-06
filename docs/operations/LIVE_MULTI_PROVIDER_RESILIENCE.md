# Live Multi-Provider Resilience Gate

**Status:** GATE DEFINED — AWAITING LIVE EXECUTION  
**Canonical implementation:** `main`

This gate closes the LIVE evidence gap identified after PR #151. It does not reopen Increment 11, Increment 12 or the frozen production-readiness queue.

## Scope

The gate exercises the canonical implementation against real external providers:

1. NVIDIA, OpenAI and Anthropic provider adapters.
2. Durable `ProviderHealthStore` success/failure state.
3. Provider catalog discovery and durable candidate staging.
4. Dynamic Director reserve-pool construction.
5. Real cross-provider Director failover.
6. Critical-decision `ModelEnsemble`.
7. `DirectorRuntime` multi-agent adversarial review.
8. Durable failover and review evidence.

The test is opt-in and writes no credentials to repository files or normal logs.

## GitHub environment

Environment: `multi-provider-live`

Required secrets:
- `NVIDIA_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

Workflow inputs provide the currently served model IDs because provider catalogs change.

## Acceptance

A green run is the LIVE acceptance evidence for PR #151's new architecture. Missing credentials, non-executable models, empty discovery results, unavailable reserves, insufficient ensemble success or insufficient adversarial reviewers fail the gate.

The gate does not authorize production execution. Controlled Live Authorization remains the activation boundary.

## Evidence already closed

LIVE-RESEARCH-DISCOVERY, LIVE-NVIDIA, LIVE-GEMINI, Increment 12 provider failover, Universal Workbench acceptance, production observability and recovery remain independently verified.
