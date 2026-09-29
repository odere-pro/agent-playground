---
title: "L-3: Router SLM: classifier that replaces part of the orchestrator's routing"
labels: ["later", "priority:P3", "phase:11-later", "area:orchestrator", "size:L", "layer:platform"]
milestone: "W12 Later"
index: 127
epic_id: L-3
depends_on: ["040 D-0", "108 O-12"]
blocks: []
epic_refs: [phase-11-later-ideas]
---

## Why

Every run that no routing rule matches calls the big planner, which costs money and time. A router SLM trained on logged runs could handle the common cases, with a confidence threshold and a fallback to the planner. It is a Phase 11 idea, so this P3 issue is a plan. It needs the recorder's logged runs (040 D-0) and the routing and planner modes (108 O-12) first.

## What

- A design note for a classifier that maps a task to a workflow or a saved plan, trained on logged runs from the recorder and the run events.
- Model choice (suggested: an encoder such as ModernBERT on CPU, the same family as the evaluator SLM).
- Where it plugs in: before the planner in `hybrid` and `planned` modes. Below the confidence threshold, routing rules and the planner decide as today.
- Labels: the workflow or plan each past run used, and whether it passed its eval.
- Metrics: accuracy at the threshold, coverage (share of runs it handles), and planner calls saved.
- Release path like any model: eval gate (037 M-2), shadow mode (047 M-3), and canary (048 M-4).
- Entry criteria, for example: enough logged planned runs, and planner cost as a large share of run cost.

## Out of scope

- Building or training the router SLM; this issue is a plan.
- Changes to the LLM router (002 G-1), which picks model routes, not workflows.
- Prompt changes (126 L-2).

## Acceptance criteria

- [ ] A design note covers the data, labels, model, threshold, fallback, and where it plugs in.
- [ ] The fallback rule is explicit: below the threshold, today's routing runs unchanged.
- [ ] Target coverage and accuracy at the threshold are set, with a cost estimate against planner spend.
- [ ] Entry criteria are measurable, with an owner who checks them.
- [ ] The plan is split into sized build issues.

## Dependencies

- Depends on: [040 D-0](040-D-0-recorder-agent.md), [108 O-12](108-O-12-routing-planner-modes.md)
- Blocks: none

## References

- Epic story: [Phase 11](../slm-agent-platform-epic-v3.md#phase-11-later-ideas)
- Epic context: [Phase 11](../slm-agent-platform-epic-v3.md#phase-11-later-ideas)
- Backlog plan: [000-plan.md](000-plan.md)
