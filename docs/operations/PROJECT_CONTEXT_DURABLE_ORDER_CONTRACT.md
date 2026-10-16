# Project context and durable order contract

This contract covers the boundary between Director conversation, durable mission submission, task execution, and the verifiable result receipt.

## Required identity flow

1. A conversation session may bind a `project_id`; it is validated against the canonical `ProjectRegistry` and stored in SQLite.
2. The dashboard conversation API accepts that `project_id` at session creation. Each inference request carries it as structured metadata.
3. Mission submission resolves the canonical project record before planning, adds the authoritative project metadata to planning context, and binds the durable mission ID to that project.
4. Every task stores `project_id` in SQLite. Worker inference metadata and governed capability constraints retain the same value. Mission and task events expose the project ID for audit.
5. Result receipts include the project ID and remain subject to the existing evidence gate; missing or invalid result evidence must not produce mission success.


## Director replanning invariants

1. Before every Director review, a project-scoped mission resolves its immutable `project_id` against the canonical `ProjectRegistry` and supplies the current project objective, status, repository, workspace, architecture reference and current state to the review model. If the canonical record cannot be resolved, the mission is blocked rather than replanned using guessed context.
2. Every follow-up task inherits the mission's canonical `project_id`. An explicit attempt to switch the task to a different project is rejected. TeamCoordinator and TaskEngine automatic-role resolution must preserve this binding.
3. Only `complete`, `blocked` and `continue` are accepted as Director review decisions. Before a `continue` decision is recorded, the follow-up DAG is phase-qualified, project-bound and validated against TaskEngine and the team policy. Invalid plans emit `director_replan_rejected`, are recorded as a blocked review and do not enqueue follow-up tasks.
4. Review, replan rejection, follow-up execution, mission reopening and terminal events carry the durable project and correlation identities. Follow-up task rows and the `team.reconfigured`, `director_decision_executed` and `mission_reopened` lifecycle receipts are committed together in the task-store transaction.
5. Automated regression coverage must prove a project-bound mission retains the same project through at least one complete replan and final result, both automatic-role conversion paths retain the project, and an invalid follow-up DAG is rejected before any follow-up row is inserted.

This section covers normal-path replanning and validation. Recovery when a process stops after recording a review decision but before committing its follow-up task graph belongs to the separate Director-restart recovery increment; this contract does not claim that interruption case is closed.

## Recovery contract

A conversation turn persisted without its assistant response is resumed on a retry using the same `request_id`. Mission IDs use deterministic call slots within the durable turn, independent of provider-generated tool-call IDs or regenerated arguments. A mission already accepted at a slot is replayed; if a retry changes the objective at that slot, the durable service rejects the conflicting reuse rather than enqueueing a second order. Distinct mission calls in one turn use distinct slots.

## Regression tests

Run the focused tests with:

```bash
python -m pytest -q tests/test_project_context_durable_order_flow.py tests/test_director_conversation.py tests/test_director_conversation_workbench.py tests/test_task_store.py tests/test_durable_director.py
```

A passing CI run is required before merging. This contract and CI do not replace local WSL/provider acceptance.
