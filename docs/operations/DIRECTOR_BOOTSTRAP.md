# Aurora Director Bootstrap

Block 3 establishes the startup contract for the Director.

## Startup contract

Before the real Director runtime is returned by `build_director_runtime()`, Aurora loads:

- Aurora Context Plane / Context Gate
- canonical project catalog
- organization and agent catalog
- skill catalog
- configured model catalog
- runtime mission-state summary
- shared durable event/mailbox index
- research knowledge/evidence index
- active Director decision policy and matching active contract when present
- evidence-backed Director decision performance
- promoted Director decision knowledge
- provider/model routing policy from the inference policy file

The resulting `DirectorBootstrapState` is immutable and exposes the startup context as system messages for the conversation layer.

## Failure behavior

Bootstrap is fail-closed. A missing/invalid model policy or incomplete required authoritative context prevents the runtime factory from returning a Director runtime.

Secrets are never loaded into the bootstrap snapshot. Provider credential values remain runtime environment concerns; only non-secret policy metadata such as credential environment-variable names may be present.

## Boundary

This block initializes the Director from one coherent authoritative snapshot.

It does **not** make ContextGate mandatory for every later inference call. Universal model-context enforcement remains Block 4.

It does not create a second task queue, journal, mailbox database or knowledge store. Existing durable task/event and research stores remain the sources of truth.
