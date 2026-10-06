# Aurora Provider Registry

Block 5 establishes the canonical runtime registry of inference providers.

## Responsibilities

The Provider Registry answers which provider implementation is available for a stable provider ID and constructs the provider adapter for a model execution.

It owns provider identity, display metadata, execution capabilities, enabled state and the provider factory. It does not own model identity or model routing scores.

## Separation of concerns

Agent: who works.

Skill: what the agent knows how to do.

Model: which inference engine is selected.

Provider: which supplier/adapter executes the selected model.

ModelRouter: which configured model should execute the request.

Provider Registry: how that provider is resolved and instantiated.

## Current registration

`nvidia_api` is the current built-in provider registration. Its adapter is imported lazily so registry initialization does not introduce import cycles.

Aurora currently registers exactly one external provider: `nvidia_api`. Other vendor adapters are intentionally absent from the runtime registry so they cannot be selected accidentally. Expanding the provider set is an explicit architecture change, not an implicit fallback.

## Boundary

Block 5 does not introduce a dynamic model catalog. `ModelSpec` remains the source of model identity and configuration until Block 6, Model Registry.

The registry does not contain credentials in its catalog. Runtime credential values stay outside the registry metadata.
