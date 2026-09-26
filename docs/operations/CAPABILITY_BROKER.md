# Aurora Capability Broker

The Capability Broker is the policy boundary between agents and tools.

## Resolution flow

1. Agent submits a named capability such as `tool:rg`.
2. Broker checks the capability allowlist.
3. Registry resolves the concrete tool and trusted source.
4. Broker checks that source against the source allowlist.
5. Authorized requests may call the Acquisition Service.
6. Acquisition returns status and provenance; the broker exposes that result to the caller.
7. Missing or untrusted capabilities are blocked rather than executed implicitly.

The broker does not execute arbitrary shell commands. Acquisition and execution are separate stages and remain auditable.

## Current boundary

The broker now supports an explicit `ensure()` path:

`CapabilityRequest -> policy -> registry -> source policy -> AcquisitionManager -> provenance`.

It still does not execute the acquired tool. Runtime execution and sandboxing remain separate stages.
