# Project context and durable order contract

This contract covers the boundary between Director conversation, durable mission submission, task execution, and the verifiable result receipt.

## Required identity flow

1. A conversation session may bind a `project_id`; it is validated against the canonical `ProjectRegistry` and stored in SQLite.
2. The dashboard conversation API accepts that `project_id` at session creation. Each inference request carries it as structured metadata.
3. Mission submission resolves the canonical project record before planning, adds the authoritative project metadata to planning context, and binds the durable mission ID to that project.
4. Every task stores `project_id` in SQLite. Worker inference metadata carries the canonical `mission_id`, `project_id` and `correlation_id`; governed capability and Workbench requests retain the same scope. Model-generated Workbench options must not override a project-bound task or conversation. Mission and task events expose the project ID for audit.
5. Versioned result receipts include `identity_version`, `mission_id`, `project_id` and `correlation_id`. The Director checks that identity against the canonical submission/task record as well as applying the existing typed-evidence gate; missing or mismatched identity evidence must not produce mission success. Legacy receipts remain readable without pretending they contain the new fields.
6. If an agent invokes Workbench while reasoning, the final model result also carries the structured action receipts from those calls. The Director validates those receipts; a failed, erroneous or truncated Workbench action cannot be hidden by a later natural-language model response claiming success.


## Director replanning invariants

1. Before every Director review, a project-scoped mission resolves its immutable `project_id` against the canonical `ProjectRegistry` and supplies the current project objective, status, repository, workspace, architecture reference and current state to the review model. If the canonical record cannot be resolved, the mission is blocked rather than replanned using guessed context.
2. Every follow-up task inherits the mission's canonical `project_id`. An explicit attempt to switch the task to a different project is rejected. TeamCoordinator and TaskEngine automatic-role resolution must preserve this binding.
3. Only `complete`, `blocked` and `continue` are accepted as Director review decisions. Before a `continue` decision is recorded, the follow-up DAG is phase-qualified, project-bound and validated against TaskEngine and the team policy. Invalid plans emit `director_replan_rejected`, are recorded as a blocked review and do not enqueue follow-up tasks.
4. Review, replan rejection, follow-up execution, mission reopening and terminal events carry the durable project and correlation identities. Follow-up task rows and the `team.reconfigured`, `director_decision_executed` and `mission_reopened` lifecycle receipts are committed together in the task-store transaction.
5. Automated regression coverage must prove a project-bound mission retains the same project through at least one complete replan and final result, both automatic-role conversion paths retain the project, an invalid follow-up DAG is rejected before any follow-up row is inserted, and model-provided Workbench options cannot switch a scoped task/conversation to another project.

This section also covers restart recovery of durable review decisions. A recorded continue decision carries the exact validated, phase-qualified follow-up task graph and team reconfiguration receipt. After restart, Aurora replays that receipt without asking the model for a replacement decision; task rows and lifecycle receipts are committed atomically. Partial legacy writes are reconciled by comparing stored rows with the receipt and adding only missing lifecycle events. A mismatched or incomplete receipt fails closed instead of guessing or enqueuing a new plan.

## Recovery contract

A conversation turn persisted without its assistant response is resumed on a retry using the same `request_id`. Mission IDs use deterministic call slots within the durable turn, independent of provider-generated tool-call IDs or regenerated arguments. A mission already accepted at a slot is replayed; if a retry changes the objective at that slot, the durable service rejects the conflicting reuse rather than enqueueing a second order. Distinct mission calls in one turn use distinct slots.

If a valid `continue` review receipt is durable but follow-up enqueue fails transiently, the mission remains non-terminal and the next runtime cycle replays the exact receipt. A storage interruption must not convert an already durable continuation into a terminal block that prevents recovery.

## Regression tests

Run the focused tests with:

```bash
python -m pytest -q tests/test_project_context_durable_order_flow.py tests/test_director_conversation.py tests/test_director_conversation_workbench.py tests/test_task_store.py tests/test_durable_director.py
```

A passing CI run is required before merging. This contract and CI do not replace local WSL/provider acceptance.
