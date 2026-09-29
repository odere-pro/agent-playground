---
title: "O-15: Save a reviewed planned run as a new workflow file"
labels: ["story", "priority:P2", "phase:7-orchestrator", "area:orchestrator", "size:S", "layer:platform"]
milestone: "W9 Orchestrator"
index: 115
epic_id: O-15
depends_on: ["108 O-12", "111 O-14", "110 C-6"]
blocks: []
epic_refs: [C.2]
---

## Why

A good plan from the smart model should not be paid for twice. Saving a reviewed planned run as a workflow file turns it into config, so the next run of the same task follows a fixed workflow instead of calling the planner. It is P2: planned runs work without it, and it only cuts planner cost and makes runs more predictable.

## What

- Export a finished planned run's plan, kept with the run, as a workflow file: `kind: Workflow`, a new `metadata.name`, and version `1.0.0`.
- Suggested: record the source `run_id` in the file's metadata.
- A person reviews the file before it is saved. The review goes through the review queue and is recorded in the audit log.
- The saved file goes through the checks and dry run before it can become active.
- Adding a routing rule that points to the new workflow is a separate config change, never automatic.
- Suggested API: `POST /v1/runs/{run_id}/save-workflow`. It returns a review ID at once, because the review waits for a person and the chassis timeout rules out long calls.

## Out of scope

- Planning itself (108 O-12).
- Choosing which planned runs are worth saving; a person decides.
- A trained router that learns from past runs (127 L-3).

## Acceptance criteria

- [ ] A finished planned run exports to a workflow file that passes `workflow.schema.json`.
- [ ] The file is not written to the config store until a reviewer approves it, and the approval is in the audit log.
- [ ] A saved workflow cannot become active before it passes the checks and dry run.
- [ ] After a routing rule points to it, the same task runs the saved workflow with the same steps and no planner call.

## Dependencies

- Depends on: [108 O-12](108-O-12-routing-planner-modes.md), [111 O-14](111-O-14-workflow-validation-rollback.md), [110 C-6](110-C-6-human-oversight.md)
- Blocks: none

## References

- Epic story: [O-15 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [C.2](../slm-agent-platform-epic-v3.md#c2)
- Backlog plan: [000-plan.md](000-plan.md)
