# Aurora Orchestration Layer

The orchestration layer is the boundary between Director/agent roles and the provider-neutral Inference Fabric.

Flow: Director -> OrchestrationEngine -> ModelRouter -> Provider.

Agents do not select or call an external model provider directly. Provider credentials are runtime-only environment variables and are never persisted in task objects or checked-in policy.

The next boundary is the Capability Broker: agent tasks will request tools/capabilities through a policy-enforcing broker before acquisition or execution.
