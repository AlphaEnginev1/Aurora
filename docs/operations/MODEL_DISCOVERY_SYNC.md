# Aurora Model Discovery / Sync

Block 7 adds governed discovery of provider model catalogs.

## Discovery boundary

Provider-specific discovery adapters implement `ModelDiscoveryProvider`.

The current built-in adapter is NVIDIA NIM. NVIDIA documents an OpenAI-compatible `GET /v1/models` endpoint that lists models currently available for inference.

Discovery is not inference. It only reads a provider catalog and produces `DiscoveredModelCandidate` records.

## Staging

Every discovered candidate is written to the durable `ModelCandidateStore`; free NVIDIA candidates are later synchronized into the authoritative registry only after the free-catalog join.

- state=`discovered`
- enabled=false
- healthy=false
- available=false

Discovery alone does not route. `activate_free_discovered()` is the governed synchronization step for models explicitly authorized by the NVIDIA Free Endpoint catalog.

Candidate metadata is kept separate from the active `ModelRegistry`.

## Promotion

Explicit manual promotion still uses `ModelPromotionValidation`. The dynamic NVIDIA free-catalog path is a separate governed synchronization path that uses the provider catalog as its policy evidence.

- policy verified
- security verified
- health verified

The approved `ModelSpec` must also match the discovered model/provider identity and be enabled, healthy and available.

Dynamic synchronization may replace the same model's runtime metadata when the authoritative free catalog is refreshed; unrelated provider models are not affected.

## Runtime integration

The production Director runtime exposes `ModelDiscoveryService` and its durable candidate store. Startup still does not perform external discovery, so boot remains deterministic.

When the Director's active provider fails and NVIDIA is next in the configured failover order, Aurora performs governed NVIDIA catalog discovery at the point of need. Discovered models remain staged in `ModelCandidateStore` and, when the NVIDIA free-endpoint policy authorizes them, automatically synchronized into the authoritative `ModelRegistry`. Paid or unknown models remain excluded.

For the failover attempt, Aurora builds an ephemeral candidate pool from the discovered catalog. Explicit model-level capabilities are honored. When NVIDIA provides no model-level capability classification, the NVIDIA provider contract supplies the provider-level `reasoning` capability. The existing `ModelRouter` scores the eligible pool, the best candidate is attempted first, and subsequent NVIDIA models remain available as additional fallback candidates when an earlier model fails.

A successful inference is the runtime health evidence required for that candidate to become the active Director provider. No fixed `AURORA_NVIDIA_MODEL_ID` is required.

## Boundary

This block establishes provider-specific discovery and safe candidate synchronization.

It does not treat the NVIDIA `/v1/models` listing alone as proof of free access; free eligibility comes from the official Build catalog. It does not replace the existing learning/performance system.

Health probing, provider-specific capability enrichment, scheduled refresh and controlled autonomous discovery can build on this contract later without bypassing governance.