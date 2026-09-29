---
title: "O-12: Routing rules and planner modes (fixed, planned, hybrid)"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:L", "layer:platform"]
milestone: "W9 Orchestrator"
index: 108
epic_id: O-12
depends_on: ["103 O-11"]
blocks: ["112 O-16", "115 O-15", "127 L-3"]
epic_refs: [C.2]
---

## Why

Routing rules make orchestration config: a task maps to a workflow, and changing the map needs no deploy. The planner covers tasks that no rule matches, using a smart model and only the top registry matches. This issue delivers the Phase 7 done-when part "switches workflows by config only". It sits on the longest chain and unblocks saving planned runs (115 O-15) and the router SLM plan (127 L-3).

## What

- `routing` rules from the orchestrator config: `match: { task }` picks a `workflow`, and `default` says what happens when nothing matches.
- Planner modes (`planner.mode`): `fixed` runs only configured workflows and rejects unmatched tasks; `planned` lets the smart model plan every run; `hybrid` uses a workflow when a rule matches and plans otherwise.
- The planner calls `planner.model_route` (for example `big-planner`) through the model port, with the task and only the registry's top matches (`top_k`, `min_trust`).
- The planner returns a plan in the workflow shape, as JSON with guided decoding, checked against `workflow.schema.json` before it runs.
- The plan runs on the workflow engine and is kept with the run.
- A new or changed rule takes effect on the next run after the config reload. Running runs keep their pinned config.
- The planner route, tokens, and cost show in the run's metrics.

## Out of scope

- Saving a good plan as a workflow file (115 O-15).
- Dry run and activation checks for workflows (111 O-14).
- A trained router SLM (127 L-3).

## Acceptance criteria

- [ ] With `mode: fixed`, a matched task runs its workflow, and an unmatched task is rejected with a clear error.
- [ ] With `mode: planned`, every task is planned, and the plan passes the workflow schema before it runs.
- [ ] With `mode: hybrid`, `simplify_text` runs `simplify-long-doc`, and an unknown task is planned.
- [ ] Pointing the `simplify_text` rule at another workflow in the config store changes the next run, with no deploy or restart.
- [ ] The planner prompt holds at most `top_k` registry entries, all at or above `min_trust`.
- [ ] A plan that fails the schema is not run; the run fails with the schema errors.

## Dependencies

- Depends on: [103 O-11](103-O-11-workflow-engine.md)
- Blocks: [112 O-16](112-O-16-event-triggers.md), [115 O-15](115-O-15-save-planned-run.md), [127 L-3](127-L-3-router-slm.md)

## References

- Epic story: [O-12 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [C.2](../slm-agent-platform-epic-v3.md#c2)
- Backlog plan: [000-plan.md](000-plan.md)
