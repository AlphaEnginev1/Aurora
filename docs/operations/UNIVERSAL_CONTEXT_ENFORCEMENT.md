# Aurora Universal Context Enforcement

Block 4 makes the Context Gate a prerequisite for Aurora-managed model inference.

## Enforcement point

The enforcement boundary is `ModelRouter.generate()`.

Before routing or provider execution, the router:

1. determines Director/global/project scope from request metadata;
2. invokes the authoritative `ContextGate`;
3. fails closed when required context is unavailable;
4. prepends the resulting authoritative manifest messages;
5. attaches `aurora_context_manifest_id`, scope and verification metadata to the request;
6. only then selects and executes the provider model.

This keeps provider adapters independent of Aurora organization and project storage while ensuring the common Aurora inference path receives a verified context contract.

## Scope rules

Director requests use `director` scope.

Specialist requests with `project_id` use `project` scope and include the agent role.

Specialist requests without a project identity use `global` scope and include the agent role.

`project_id` is an optional inference metadata field and is also accepted by `OrchestrationEngine.run_task()` for callers that already possess canonical project identity.

## Failure behavior

Enforcement is fail-closed. A router configured with `enforce_context=True` but without a Context Gate rejects inference.

The router also rejects inference when runtime state remains unknown after the gate prepares the manifest.

## Boundary

This block protects the common Aurora inference path. Direct calls to provider classes are provider-contract tests or external integrations; Aurora runtime execution reaches providers through `ModelRouter`.

Provider Registry and Model Registry remain later blocks in the official worklist.