# Aurora Inference Fabric v1

Aurora agents do not call model providers directly. They submit provider-neutral inference requests to the Model Router.

## Design

- ModelSpec describes a model, provider, endpoint, capabilities, and context window.
- ModelRouter selects an enabled model by explicit model id or requested capability.
- Provider adapters translate the neutral request into a provider API call.
- NVIDIA API is the first remote provider. Credentials are supplied at runtime through NVIDIA_API_KEY and are never committed to the repository.
- Local GPU and additional remote providers are separate adapters and can be added without changing agent code.

## NVIDIA hosted inference

NVIDIA's API Catalog provides NVIDIA-hosted NIM endpoints for development and prototyping. Aurora treats the endpoint as a configurable provider rather than assuming a particular model or permanent free quota.

## Safety and operations

1. API keys stay outside Git and CI logs.
2. Models are disabled by default in the checked-in example policy until credentials and the desired endpoint have been verified.
3. Agents request capabilities, not providers.
4. Provider-specific failures remain inside the provider adapter.
5. Future fallback routing must remain policy-controlled and auditable.

## Next increments

1. Load inference-policy.json into the router.
2. Add health, latency telemetry, and provider audit events.
3. Add a second remote provider adapter behind the same interface.
4. Add local GPU as an optional backend.
5. Connect Director and Orchestrator agent execution to the router.
