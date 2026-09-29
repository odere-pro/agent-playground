---
title: "E-2: Evaluator agreement test against human labels"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:evaluator", "size:S", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 74
epic_id: E-2
depends_on: ["073 E-1", "030 S-4"]
blocks: ["075 E-3"]
epic_refs: [E.5]
---

## Why

An evaluator is only safe to use if it agrees with people. This test compares the evaluator SLM with human labels, side by side with the big-model judge, before it can replace that judge. It is evidence for AI Act Art. 15 (accuracy) and completes the evaluator part of the Phase 4 done-when.

## What

- The test set: the `holdout` split plus the S-4 held-out test set (≥ 300 records), all with human labels, none used in training.
- Metrics at the 0.95 threshold: agreement rate, Cohen's kappa, recall on the fail class (dropped facts), and false-pass rate.
- The big-model judge is scored on the same set, as the baseline.
- The pass bar is written down before the first run. Suggested: kappa ≥ 0.8, and fail-class recall no lower than the judge's.
- Results per slice (source model, text length, and tag), so weak spots show.
- The test runs in CI as a gate: a new evaluator version is promoted only if it passes.
- The report is logged in MLflow against the evaluator model version.

## Out of scope

- Training the evaluator (073 E-1).
- Swapping it into the harness and the shadow check on live traffic (075 E-3).
- Evaluators for other tasks.

## Acceptance criteria

- [ ] The test runs on the `holdout` split plus the S-4 held-out set (≥ 300 human-labeled records), with no overlap with training data.
- [ ] The report shows agreement rate, kappa, fail-class recall, and false-pass rate for the SLM and the big-model judge side by side.
- [ ] The pass bar is recorded before the first run and checked automatically.
- [ ] The report includes results per slice.
- [ ] CI blocks promotion of an evaluator version that fails the bar.
- [ ] The report is in MLflow, linked to the evaluator model version.
- [ ] Phase 4 done-when (evaluator): the chosen evaluator SLM passes the bar on the test set.

## Dependencies

- Depends on: [073 E-1](073-E-1-evaluator-slm.md), [030 S-4](030-S-4-hand-review-test-set.md)
- Blocks: [075 E-3](075-E-3-evaluator-replaces-judge.md)

## References

- Epic story: [E-2 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
