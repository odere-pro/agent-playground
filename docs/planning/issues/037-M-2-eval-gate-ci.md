---
title: "M-2: Eval gate in CI: a new model ships only if it beats the current one"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:lifecycle", "size:M", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 37
epic_id: M-2
depends_on: ["028 S-3", "030 S-4", "032 M-1", "020 X-8"]
blocks: ["047 M-3", "049 M-5"]
epic_refs: [E.5, principles]
---

## Why

This is P0 because of the epic principle: nothing ships without an eval. The gate makes the rule automatic, so a new model or prompt ships only if it beats the current one on the golden set. Until the golden set agent exists in Phase 4, it uses the 030 S-4 held-out test set as the golden set.

## What

- A CI job that runs when a new model version is registered in MLflow, or when a change to the simplifier prompt is proposed.
- The candidate and the current model scored on the golden set with the S-3 metrics.
- A pass rule (suggested, to confirm with the owner): meet the hard targets (`facts_kept` ≥ 0.95, no banned words), be no worse than the current model on any metric beyond a set tolerance, and be better on `facts_kept` or token reduction.
- On pass: the version is marked promoted in MLflow, and a `models.model.promoted.v1` event is published. The route then changes through a reviewed router config change.
- On fail: promotion is blocked, with a report.
- The golden set location as config, so the Phase 4 golden set replaces the S-4 test set with no code change.
- Eval results for both models attached to the CI run and to the MLflow model version.

## Out of scope

- Shadow mode, canary rollout, and one-step rollback (047 M-3, 048 M-4).
- Evals for quantized variants (049 M-5).
- The golden set agent and versioned golden sets (064 D-1, 069 D-7).

## Acceptance criteria

- [ ] Registering a new model version starts the gate in CI with no manual step.
- [ ] A candidate worse than the current model on `facts_kept` fails, and promotion is blocked with a report.
- [ ] A candidate below `facts_kept` 0.95 fails, even when there is no current model.
- [ ] A passing candidate is marked promoted in MLflow, and a `models.model.promoted.v1` event is published.
- [ ] A change to the simplifier prompt runs the same gate before release.
- [ ] Pointing the gate at another dataset version needs a config change only.
- [ ] Eval results for both models are attached to the CI run and to the MLflow model version.

## Dependencies

- Depends on: [028 S-3](028-S-3-simplifier-metrics.md), [030 S-4](030-S-4-hand-review-test-set.md), [032 M-1](032-M-1-mlflow.md), [020 X-8](020-X-8-event-broker.md)
- Blocks: [047 M-3](047-M-3-shadow-mode.md), [049 M-5](049-M-5-quantized-variants.md)

## References

- Epic story: [M-2 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [Principles](../slm-agent-platform-epic-v3.md#principles)
- Backlog plan: [000-plan.md](000-plan.md)
