# NVIDIA-only free-model resilience

Aurora's active inference architecture has one external provider: NVIDIA NIM.

## Runtime contract

The Director uses the provider-neutral Aurora router. The router and failover controller may select any eligible NVIDIA Free Endpoint model discovered from the live NVIDIA catalog. The checked-in NVIDIA models are seed metadata only; they are never a hard ceiling.

When an NVIDIA model fails, health is tracked at `provider_id + model_id` granularity. Aurora can therefore continue to another NVIDIA model from the same provider. When no eligible NVIDIA model remains, the runtime fails closed and preserves the failure evidence.

Gemini, OpenAI and Anthropic are not part of the active provider registry, Director settings, dashboard settings, or LIVE credential contract.

## LIVE acceptance

The workflow `.github/workflows/live-multi-provider-resilience.yml` is the NVIDIA-only LIVE gate. It requires `NVIDIA_API_KEY`, performs dynamic Free Endpoint discovery, and executes a real reasoning request against NVIDIA. Current status: BLOCKED until the real inference stage succeeds; runs 37814164201 and 37813096077 returned HTTP 403. Never count catalog discovery alone as live inference success.

Historical Gemini and multi-vendor acceptance records remain archived as evidence of previous architecture states; they are not active runtime dependencies.
