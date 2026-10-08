# NVIDIA model routing incident audit — 2026-10-08

## Executive finding

The incident was **not caused by the NVIDIA provider adapter or by the NVIDIA API key**.

The old and new revisions use the same NVIDIAAPIProvider implementation and the same hosted endpoint contract. The regression exposed by the new LIVE gate is in the **model-selection/discovery contract**:

1. Aurora's checked-in NVIDIA seed models were still marked enabled=true in config/inference-policy.json.
2. The NVIDIA dynamic Free Endpoint catalog was intended to be authoritative, but nvidia_discovery.py silently converted catalog failures into an empty set.
3. free_policy.is_discovered_free_model() allowed a checked-in NVIDIA seed record to authorize a model even when the current official catalog had not confirmed it.
4. The canonical startup path only refreshed/activated dynamic NVIDIA models when the Director was explicitly configured as nvidia_api. With the normal router mode, Aurora could start directly from the stale checked-in seed pool.
5. The new LIVE resilience test intentionally bypasses the persisted operator-selected model and asks the router to choose from the dynamically discovered free pool. That exposed a stale/insufficient candidate pool and selected nvidia/nemotron-3-ultra-550b-a55b, which then returned HTTP 403.
6. The router fallback added in PR #170 is **not the root cause**. It can retry another candidate when another eligible candidate exists; it cannot manufacture a second candidate when discovery only produced one eligible model.

NVIDIA's current official catalog is dynamic and currently exposes dozens of Free Endpoint-labelled models; the hosted API uses POST /v1/chat/completions. Catalog membership and successful execution are separate runtime facts.

## Old vs new comparison

### NVIDIA execution adapter

aurora/inference/providers/nvidia_api.py is materially unchanged between the previous working revision and the new revision.

It still:
- reads NVIDIA_API_KEY;
- sends Authorization: Bearer ...;
- calls https://integrate.api.nvidia.com/v1/chat/completions;
- uses the selected ModelSpec.id;
- converts HTTP failures into typed provider errors.

Therefore this layer is not the regression.

### Router

The new revision added model-level retry after execution failure:
ModelRouter.generate() -> route() -> execute -> record failure -> rebuild allowed_models -> route again.

That change is correct and covered by deterministic tests. It depends on RoutingDecision.candidates containing more than the failed model.

The LIVE failure selected only nvidia/nemotron-3-ultra-550b-a55b. Therefore the important question is why the candidate pool did not contain the working alternatives. The answer is upstream: discovery/policy/startup eligibility.

### NVIDIA discovery

Before this audit, NVIDIAModelDiscoveryProvider.discover() did this:

- call the official Build catalog;
- if the catalog call failed for any reason, silently use free_catalog_ids = set();
- call NVIDIA /v1/models;
- use static free-policy metadata and row metadata to classify models as free.

This violated the stated architecture. A failure of the authoritative catalog could silently degrade into a stale static allowlist.

### Free-model policy

The previous implementation treated a static policy record with tier=free as sufficient for is_discovered_free_model().

That was inconsistent with the policy's own documentation, which says checked-in NVIDIA entries are seed metadata and the live Free Endpoint catalog is authoritative.

The corrected rule is:

> For NVIDIA dynamic free_endpoint mode, a model is currently free **only when the current official catalog confirms it**. Static records may enrich metadata but cannot authorize current eligibility.

### Startup

The canonical runtime constructed the router from the checked-in inference policy and only invoked dynamic discovery activation when the configured Director provider was nvidia_api.

Aurora's canonical default is AURORA_DIRECTOR_PROVIDER=router.

That created a gap: normal router startup could operate from the stale seed pool instead of first constructing the live NVIDIA Free Endpoint pool.

The corrected startup path refreshes/activates NVIDIA dynamic models before downstream model snapshots are consumed, including normal router mode.

## Why old Aurora could answer while the new LIVE gate failed

The old working path and the new LIVE gate were not selecting models in the same way.

The old/local path can use the persisted operator-selected model and therefore continue using a model that is known to work in that local environment.

The new LIVE acceptance deliberately asks the routing layer to discover and select an eligible NVIDIA Free Endpoint model. That is a different contract and correctly exposed that Aurora's dynamic catalog source of truth was not yet enforced.

So:

- **Old path working does not prove dynamic discovery was correct.**
- **New LIVE failure does not prove the NVIDIA provider/key is broken.**
- It proves the new dynamic model-selection path was not yet enforcing its own catalog contract.

## Correct runtime contract after this audit

The NVIDIA path is now intended to be:

official NVIDIA Free Endpoint catalog
→ NVIDIA /v1/models
→ intersection of currently served + currently Free Endpoint
→ capability classification
→ dynamic candidate registry
→ router scoring
→ execution
→ model-level health evidence
→ retry another eligible model on execution failure.

Checked-in models remain metadata seeds only.

A model can separately be:

- catalog-confirmed;
- served by /v1/models;
- capability-eligible;
- execution-successful;
- temporarily unhealthy/cooling down.

These states must never be conflated.

## Regression protections added in this change

1. NVIDIA dynamic free policy fails closed when a seed model is not confirmed by the current catalog.
2. NVIDIA discovery fails closed if the authoritative Free Endpoint catalog cannot be read, instead of silently reverting to static seeds.
3. NVIDIA row metadata such as free_endpoint=true cannot override the official catalog.
4. Static NVIDIA seed models in the inference policy are no longer active runtime models by default.
5. Canonical startup refreshes dynamic NVIDIA models even when Director mode is router.
6. Downstream skill/project model snapshots are rebuilt after dynamic model activation.
7. Tests explicitly cover the stale-seed regression and the router-mode startup path.

## Operational rule for future changes

Do not reintroduce any of these patterns:

- static NVIDIA model IDs as the runtime ceiling;
- enabled=true seed models presented as current free eligibility;
- swallowing Free Endpoint catalog failures;
- treating /v1/models availability as proof of Free Endpoint eligibility;
- treating Free Endpoint catalogue membership as proof of successful inference;
- testing dynamic routing with a hard-coded model list;
- assuming one NVIDIA model failure means the NVIDIA provider itself is unavailable.

The correct unit of health is **provider + model**, not provider alone.

## Evidence

The incident was reproduced in the LIVE gate with:

selected_model = nvidia/nemotron-3-ultra-550b-a55b

followed by:

ProviderHTTPError: NVIDIA HTTP request failed with status 403.

The current NVIDIA documentation confirms the hosted LLM API endpoint is https://integrate.api.nvidia.com with POST /v1/chat/completions.

The official NVIDIA Build catalog is dynamic and currently exposes a large Free Endpoint catalogue. This is precisely why Aurora must discover it at runtime rather than treating checked-in model IDs as authoritative.

This document is the permanent incident record and regression contract. Future NVIDIA routing work must preserve these invariants.
