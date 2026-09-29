---
title: "O-8: Cancel and human approval steps"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:S", "layer:platform"]
milestone: "W9 Orchestrator"
index: 109
epic_id: O-8
depends_on: ["100 O-2", "107 O-6"]
blocks: ["110 C-6", "114 P-2b"]
epic_refs: [C.2, E.5]
---

## Why

Some steps must wait for a person, and any run must be stoppable. Approval steps and cancel are the orchestrator side of human oversight, which AI Act Art. 14 asks for. This issue builds the mechanics in the engine; the review queue and audit wiring follow right after in 110 C-6.

## What

- Approval steps: the run pauses before a step and waits for an approve or reject decision without holding a worker (a Temporal signal and wait).
- Triggers from `approvals.require_for` in the orchestrator config: `external_tool` (the step calls a tool or agent whose registry trust level is external) and `cost_above_usd_2` (the run's spend passes $2, from the run budget totals).
- Suggested: an explicit approval step type in workflow files, added to `workflow.schema.json`.
- An approve and reject API on the orchestrator, behind a write scope (suggested: `POST /v1/runs/{run_id}/approvals/{step_id}`).
- Approve continues the run from the paused step. Reject stops it.
- Cancel: a stop control that cancels the Temporal workflow, stops in-flight steps (all parallel branches too), starts no new step, and marks the run `cancelled` (suggested: `POST /v1/runs/{run_id}/cancel`, the backend of `run_cancel`).
- Suggested: an approval timeout, after which the run stops.
- The approve, reject, and cancel calls return at once, because the chassis timeout rules out long calls. The run status shows when the run has stopped.

## Reuse

- **Use:** Temporal signals and updates for approve and cancel.
- **Watch:** HumanLayer's open-source SDK is deprecated, so it is not used.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The review queue in the Review UI, the `oversight.*` events, and audit records for decisions (110 C-6).
- The `run_cancel` MCP tool (114 P-2b).

## Acceptance criteria

- [ ] A step that calls an external tool pauses the run until it is approved, and then runs.
- [ ] A rejected approval stops the run, and no later step runs.
- [ ] A run whose spend passes $2 pauses for approval before its next step.
- [ ] A paused run survives a pod restart and still waits for the decision.
- [ ] Cancel during a fan-out stops all branches, and the run status becomes `cancelled`.
- [ ] Only callers with the write scope can approve, reject, or cancel.

## Dependencies

- Depends on: [100 O-2](100-O-2-temporal-checkpoints.md), [107 O-6](107-O-6-run-budgets.md)
- Blocks: [110 C-6](110-C-6-human-oversight.md), [114 P-2b](114-P-2b-mcp-run-memory-config-tools.md)

## References

- Epic story: [O-8 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [C.2](../slm-agent-platform-epic-v3.md#c2) · [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
