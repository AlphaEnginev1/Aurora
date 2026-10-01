# Aurora Model Registry

Block 6 establishes the canonical runtime catalog of Aurora inference models.

## Source of truth

`ModelRegistry` owns the authoritative model identities used by Aurora's routing path.

Each registered `ModelSpec` retains provider identity, endpoint, capabilities, specialties, context capacity, availability and routing scores.

The registry performs strict validation for model identity, provider reference, endpoint, context window and score ranges. When a `ProviderRegistry` is supplied, model registration also verifies that the referenced provider exists and is enabled.

## Runtime integration

`ModelRouter` accepts an optional `ModelRegistry`.

When a registry is present, routing, selection and provider execution resolve models from that registry rather than from a stale local list.

Production factories create one validated `ModelRegistry` from the inference policy and share it with:

- `ModelRouter`
- `AuroraContextPlane`

This keeps routing context and execution aligned to the same model catalog.

For backward compatibility, direct `ModelRouter(models=[...])` construction remains supported. That compatibility path creates a registry snapshot without requiring provider validation.

## Separation from Provider Registry

Provider Registry answers:

`provider_id -> provider implementation`

Model Registry answers:

`model_id -> model specification`

Model routing continues to consume learned performance and request-specific factors. The Model Registry does not own the learning store.

## Boundary

This block provides the canonical model catalog but does not discover or synchronize models from external providers.

Dynamic provider model discovery, catalog reconciliation, health refresh and controlled sync remain Block 7.