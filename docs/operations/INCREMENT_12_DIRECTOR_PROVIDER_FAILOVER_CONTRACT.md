# Increment 12 — Historical Director Provider Failover Contract

**Status:** HISTORICAL CONTRACT — superseded by the canonical NVIDIA-only provider runtime.
**Historical scope:** Gemini-to-NVIDIA/router replacement was implemented and tested against an earlier runtime contract. It is not the provider topology currently declared by `README.md` and `aurora/orchestration/factory.py`.

## Current canonical contract

The canonical runtime uses `ModelRouter` with NVIDIA NIM as its only external provider and a free-only policy. Resilience therefore means switching between eligible NVIDIA Free Endpoint models, not silently reactivating Gemini, OpenAI, or Anthropic.

The current acceptance is not considered live-verified until all of the following are proven against the current `main` commit:

1. The live NVIDIA Free Endpoint catalog is retrieved and staged.
2. At least one discovered free model is eligible for reasoning.
3. A real inference request succeeds through Aurora's network gateway.
4. A controlled primary-model failure results in a successful eligible NVIDIA reserve model, with no duplicate mission/task work.
5. The durable provider/model transition is recorded without credentials.
6. Exhausting the eligible reserve pool fails closed and does not persist a failed candidate.

## Historical invariants retained

- Provider replacement does not recreate mission, task, project, conversation, or knowledge state.
- A candidate becomes active only after a successful request.
- Credentials and sensitive response bodies are excluded from durable failover evidence.
- Mission ownership and fencing remain under `DirectorOwnershipStore`.
- If no eligible candidate succeeds, the Director fails closed.

## Evidence policy

Historical Gemini-based CI/live evidence remains historical and must not be presented as proof that the current NVIDIA-only runtime passes live inference. The current live NVIDIA gate last failed on 2026-10-08 with HTTP 403 during inference (runs [37814164201](https://github.com/AlphaEnginev1/Aurora/actions/runs/37814164201) and [37813096077](https://github.com/AlphaEnginev1/Aurora/actions/runs/37813096077)). A successful rerun against a currently authorized model is required before marking this gate LIVE VERIFICADO.

Increment 11 and the historical Increment 12 acceptance are not reopened. This document records the compatibility boundary and the evidence required for the current provider topology.
