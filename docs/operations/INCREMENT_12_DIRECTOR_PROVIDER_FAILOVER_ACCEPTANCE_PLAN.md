# Provider Failover Acceptance — Current Runtime and Historical Evidence

## Status

The deterministic acceptance for the historical Gemini-to-NVIDIA/router topology remains historical evidence. It does not establish live readiness of the current NVIDIA-only runtime.

The canonical runtime is free-only and uses NVIDIA NIM through Aurora's inference router. The current live acceptance is blocked by an external HTTP 403 returned during actual NVIDIA inference:

- [Run 37814164201](https://github.com/AlphaEnginev1/Aurora/actions/runs/37814164201)
- [Run 37813096077](https://github.com/AlphaEnginev1/Aurora/actions/runs/37813096077)

Both failures occurred in `test_live_nvidia_only_catalog_and_inference`, after the live catalog step, during the actual inference request. The provider adapter deliberately does not log the response body or credentials. HTTP 403 may indicate key permissions, model entitlement, or endpoint access; the workflow cannot read or repair the secret itself.

## Required current acceptance sequence

1. Retrieve the live NVIDIA serving catalog through the configured network gateway.
2. Resolve at least one model whose live catalog metadata explicitly establishes Free Endpoint eligibility.
3. Execute real inference against an eligible model.
4. Force a controlled failure for the selected model without corrupting a live request.
5. Confirm the reserve selector chooses another eligible model and the same request continues.
6. Verify durable transition evidence, no secret leakage, and no duplicated mission/task execution.
7. Exhaust eligible candidates and verify fail-closed behavior with no failed candidate activation.
8. Re-run after the NVIDIA account's access to the selected free model and endpoint has been verified.

## Historical deterministic acceptance

Earlier deterministic tests for provider failover, mission continuity, conversation continuity, fail-closed behavior, and restart persistence remain regression tests. They are not a substitute for the current live test.

No external live authorization is enabled by this acceptance plan.
