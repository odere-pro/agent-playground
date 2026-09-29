---
title: "O-6: Budgets per run: max tokens, cost, steps, and time"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:S", "layer:platform"]
milestone: "W9 Orchestrator"
index: 107
epic_id: O-6
depends_on: ["100 O-2", "103 O-11"]
blocks: ["109 O-8"]
epic_refs: [C.2]
---

## Why

A run with no limits can loop or spend without bound, especially when a smart model plans it. Budgets per run cap tokens, cost, steps, and time, and stop the run when one is hit. This issue delivers the Phase 7 done-when part "stops a run that exceeds its budget".

## What

- Run limits from the orchestrator config `limits`: `max_steps`, `max_tokens`, `max_cost_usd`, `timeout_s`.
- The workflow file `budget` (for example `max_tokens: 20000`) applies too; the stricter limit wins.
- Token and cost totals add up from each step's response `metrics`, which carry the router's token and cost counts.
- Before each step, check the remaining budget. Each agent call gets the remaining budget in its envelope `budget`, so no single call can overspend.
- `timeout_s` becomes the Temporal workflow run timeout.
- When a limit is hit, the run stops, keeps its checkpoints, and reports which limit it hit and the totals so far (suggested status: `stopped_budget`).
- Budget use per run shows in metrics and traces.

## Out of scope

- The approval step when cost passes `cost_above_usd_2` (109 O-8).
- Per-call budgets inside each agent (017 H-4).
- Cost dashboards (120 X-7).

## Acceptance criteria

- [ ] A run with more than `max_steps` steps stops before step `max_steps + 1` starts.
- [ ] A fake agent that reports high token use stops the run once `max_tokens` is passed.
- [ ] A fake agent that reports cost stops the run once `max_cost_usd` is passed.
- [ ] A run longer than `timeout_s` is stopped by Temporal.
- [ ] When the workflow `budget` is lower than the config limit, the workflow budget applies.
- [ ] The envelope `budget` sent to each agent is never more than the run's remaining budget.
- [ ] A stopped run reports the limit it hit and its totals in the run status.

## Dependencies

- Depends on: [100 O-2](100-O-2-temporal-checkpoints.md), [103 O-11](103-O-11-workflow-engine.md)
- Blocks: [109 O-8](109-O-8-cancel-approval.md)

## References

- Epic story: [O-6 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [C.2](../slm-agent-platform-epic-v3.md#c2)
- Backlog plan: [000-plan.md](000-plan.md)
