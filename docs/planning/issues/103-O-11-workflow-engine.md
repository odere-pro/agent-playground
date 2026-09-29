---
title: "O-11: Workflow engine that runs chain, fan_out, and compare patterns from workflow files"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:L", "layer:platform"]
milestone: "W9 Orchestrator"
index: 103
epic_id: O-11
depends_on: ["098 O-10", "100 O-2"]
blocks: ["105 O-4", "106 O-5", "107 O-6", "108 O-12", "111 O-14", "112 O-16"]
epic_refs: [C.2, F.7]
---

## Why

This is the core of "orchestration is config": a workflow file in the config store decides what runs, not code. The engine reads workflow files and runs them as Temporal workflows. It unblocks fan-out, compare, routing, and validation, and it sits on the longest chain in the backlog.

## What

- Load a workflow file (`kind: Workflow`) through the pinning helper (O-13), checked against `workflow.schema.json`.
- Run `chain` fully: steps run one after another, and each output feeds the next step.
- Run the base of `fan_out` (parallel copies of a step over a list of inputs, results collected in order) and of `compare` (several candidate steps on the same input, all outputs collected).
- Resolve each step's agent: `{ task }` through registry search (main pick plus ranked fallbacks), `{ name }` directly.
- Step `input` references, such as `input: merged`, point to an earlier step's output.
- `parallel: true` steps run as parallel activities, each with its own checkpoint.
- `on_failure`: use `retries`, then `fallback_route`, `skip`, or `stop`. Suggested reading of `fallback_route`: call the step's next ranked registry fallback; confirm in design.

## Reuse

- **Use:** the CNCF Serverless Workflow DSL 1.0 as the model for the YAML shape.
- **Build:** the thin interpreter from our workflow YAML to Temporal workflows.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Section split with overlap, merge, and the consistency pass (105 O-4).
- Evaluator scoring, the tie rule, and sending outputs to the golden set (106 O-5).
- Routing rules and the planner (108 O-12); here the run names its workflow.
- Steps on agent pools over task queues (104 O-3); until then the engine calls agents through the agent port.

## Acceptance criteria

- [ ] A three-step `chain` workflow in the config store runs end to end, and each step gets the previous output.
- [ ] A `fan_out` workflow with five inputs runs five parallel steps and returns the results in input order.
- [ ] A `compare` workflow runs two candidate agents on the same input and returns both outputs.
- [ ] Each `on_failure.then` value (`fallback_route`, `skip`, `stop`) has a test with a failing fake agent, after `retries` are used up.
- [ ] A workflow file that fails the schema is rejected before the run starts.
- [ ] For each pattern, killing a pod mid-run resumes at the last checkpoint.

## Dependencies

- Depends on: [098 O-10](098-O-10-orchestrator-schemas.md), [100 O-2](100-O-2-temporal-checkpoints.md)
- Blocks: [105 O-4](105-O-4-fan-out-fan-in.md), [106 O-5](106-O-5-compare-runs.md), [107 O-6](107-O-6-run-budgets.md), [108 O-12](108-O-12-routing-planner-modes.md), [111 O-14](111-O-14-workflow-validation-rollback.md), [112 O-16](112-O-16-event-triggers.md)

## References

- Epic story: [O-11 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [C.2](../slm-agent-platform-epic-v3.md#c2) · [F.7](../slm-agent-platform-epic-v3.md#f7)
- Backlog plan: [000-plan.md](000-plan.md)
