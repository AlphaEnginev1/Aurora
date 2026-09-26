# Aurora Capability Broker

The Capability Broker is the policy boundary between agents and tools.

## Resolution flow

1. Agent submits a named capability such as `tool:rg`.
2. Broker checks the capability allowlist.
3. Registry resolves the concrete tool and trusted source.
4. Broker checks that source against the source allowlist.
5. Authorized requests proceed to acquisition and verification.
6. Missing or untrusted capabilities are blocked rather than executed implicitly.

The broker does not execute arbitrary shell commands. Acquisition and execution are separate stages and remain auditable.

## Current boundary

This increment implements deterministic authorization and resolution. It deliberately does not install packages yet. The next increment adds the acquisition planner/executor with idempotency, verification and audit records.
