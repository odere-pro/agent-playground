---
title: "O-5: Compare runs with an evaluator and a tie rule"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 106
epic_id: O-5
depends_on: ["103 O-11", "068 D-3"]
blocks: []
epic_refs: [F.7]
---

## Why

Compare runs pick the best output when there is more than one candidate, such as a new SLM next to the current one. They also feed the golden set with scored alternatives, which trains better models and evaluators. This issue delivers the Phase 7 done-when part "runs a compare job".

## What

- The `compare` pattern: run the same job several times or on several agents, as listed in the workflow steps (suggested: `repeat` on a step for several runs of one agent).
- Score every candidate with the `evaluate` agent (for example `evaluator-facts`) on the same input.
- Keep the best candidate and return it. The response lists every candidate with its agent, score, and cost.
- A tie rule for equal scores (suggested: an `evaluate.tie_rule` field in `workflow.schema.json`, default `cheapest`, then `fastest`, following the cost rules in J).
- Candidates below `evaluate.threshold` are never kept. If none pass, `on_failure` applies.
- All candidate outputs, with scores and a winner flag, go to the golden set through its import API (068 D-3).
- A `compare-simplifiers` workflow file in the config store as the reference job.

## Out of scope

- Run and step events for compare runs (112 O-16).
- Promoting a model based on compare results, which goes through the eval gate and canary (037 M-2, 048 M-4).
- Routing a task to the compare workflow by rule (108 O-12).

## Acceptance criteria

- [ ] `compare-simplifiers` runs one input on two simplifier candidates (for example the SLM route and the big-model route) and returns the higher-scored output.
- [ ] A step with `repeat: 3` runs three times, and each run is scored.
- [ ] A test with equal scores always picks the same candidate, by the tie rule.
- [ ] When no candidate passes the threshold, the run follows `on_failure`.
- [ ] All candidate outputs show in the golden set with their scores, the winner flag, and the `run_id`.

## Dependencies

- Depends on: [103 O-11](103-O-11-workflow-engine.md), [068 D-3](068-D-3-import.md)
- Blocks: none

## References

- Epic story: [O-5 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [F.7](../slm-agent-platform-epic-v3.md#f7)
- Backlog plan: [000-plan.md](000-plan.md)
