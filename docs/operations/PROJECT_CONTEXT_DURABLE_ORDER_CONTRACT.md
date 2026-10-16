# Project context and durable order contract

This contract covers the boundary between Director conversation, durable mission submission, task execution, and the verifiable result receipt.

## Required identity flow

1. A conversation session may bind a `project_id`; it is validated against the canonical `ProjectRegistry` and stored in SQLite.
2. The dashboard conversation API accepts that `project_id` at session creation. Each inference request carries it as structured metadata.
3. Mission submission resolves the canonical project record before planning, adds the authoritative project metadata to planning context, and binds the durable mission ID to that project.
4. Every task stores `project_id` in SQLite. Worker inference metadata and governed capability constraints retain the same value. Mission and task events expose the project ID for audit.
5. Result receipts include the project ID and remain subject to the existing evidence gate; missing or invalid result evidence must not produce mission success.

## Recovery contract

A conversation turn persisted without its assistant response is resumed on a retry using the same `request_id`. Mission IDs use deterministic call slots within the durable turn, independent of provider-generated tool-call IDs or regenerated arguments. A mission already accepted at a slot is replayed; if a retry changes the objective at that slot, the durable service rejects the conflicting reuse rather than enqueueing a second order. Distinct mission calls in one turn use distinct slots.

## Regression tests

Run the focused tests with:

```bash
python -m pytest -q tests/test_project_context_durable_order_flow.py tests/test_director_conversation.py tests/test_director_conversation_workbench.py tests/test_task_store.py tests/test_durable_director.py
```

A passing CI run is required before merging. This contract and CI do not replace local WSL/provider acceptance.

## Director re-planning and restart recovery

6. Every Director review for a project-scoped mission is given the current canonical project record again (ID, objective, repository/workspace references and current state). If that record cannot be resolved, Aurora records the rejection and blocks the mission instead of replanning without authoritative context.
7. A `continue` review receipt stores the exact validated, phase-qualified follow-up task graph, its project IDs, and the team reconfiguration before any follow-up enqueue is attempted.
8. The follow-up task rows, `director_decision_executed` receipt and `mission_reopened` event commit in one SQLite transaction. Their correlation ID and project ID match the original durable order.
9. On restart, DirectorRuntime replays the persisted review decision before calling the planner again. The same task graph is enqueued exactly once. If the durable graph, phase rows, project binding or execution receipt contradict one another, recovery records `director_review_recovery_rejected` and blocks rather than guessing or creating alternate work.
10. A process interruption after `director_review_started` but before a durable review outcome does not consume a review round. The bounded review budget counts recorded review outcomes, allowing the same round to be retried after restart.

## Additional point 4/5 acceptance

The regression test `test_project_replanning_and_restart_replay_preserve_one_mission_and_context` simulates a process interruption after the first `continue` decision has been persisted but before its follow-up graph is enqueued. It then constructs a fresh DirectorRuntime over the same SQLite store and verifies the exact graph, project identity, correlation, lifecycle markers, and single terminal outcome. This automated test does not claim a real WSL process-kill/provider test.

