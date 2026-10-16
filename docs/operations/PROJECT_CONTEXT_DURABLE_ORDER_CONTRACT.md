# Project context and durable order contract

This contract covers the boundary between Director conversation, durable mission submission, task execution, and the verifiable result receipt.

## Required identity flow

1. A conversation session may bind a `project_id`; it is validated against the canonical `ProjectRegistry` and stored in SQLite.
2. The dashboard conversation API accepts that `project_id` at session creation. Each inference request carries it as structured metadata.
3. Mission submission resolves the canonical project record before planning, adds the authoritative project metadata to planning context, and binds the durable mission ID to that project.
4. Every task stores `project_id` in SQLite. Worker inference metadata and governed capability constraints retain the same value. Mission and task events expose the project ID for audit.
5. Result receipts include the project ID and remain subject to the existing evidence gate; missing or invalid result evidence must not produce mission success.

## Recovery contract

A conversation turn persisted without its assistant response is resumed on a retry using the same `request_id`. Mission IDs are deterministic for the durable turn and objective, so a mission already accepted by the durable store is replayed instead of enqueued a second time. Different objectives in one turn receive different mission IDs.

## Regression tests

Run the focused tests with:

```bash
python -m pytest -q tests/test_project_context_durable_order_flow.py tests/test_director_conversation.py tests/test_director_conversation_workbench.py tests/test_task_store.py tests/test_durable_director.py
```

A passing CI run is required before merging. This contract and CI do not replace local WSL/provider acceptance.
