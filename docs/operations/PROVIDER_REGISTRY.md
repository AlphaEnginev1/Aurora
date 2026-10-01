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

Future providers such as Gemini, Claude or local inference can be registered under their provider IDs without adding vendor-specific branches to `ModelRouter`.

## Boundary

Block 5 does not introduce a dynamic model catalog. `ModelSpec` remains the source of model identity and configuration until Block 6, Model Registry.

The registry does not contain credentials in its catalog. Runtime credential values stay outside the registry metadata.
