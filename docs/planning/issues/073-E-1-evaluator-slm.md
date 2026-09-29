---
title: "E-1: First evaluator SLM (ModernBERT or DeBERTa) trained on approved records"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:evaluator", "size:L", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 73
epic_id: E-1
depends_on: ["033 S-5", "065 D-2", "070 D-4"]
blocks: ["074 E-2"]
epic_refs: [I]
---

## Why

Judging every simplifier call with a big model costs money on every request. A small encoder trained on approved records can do the same job on CPU. It needs approved, human-labeled records from the review UI (065 D-2), versioned exports (070 D-4), and the training pipeline (033 S-5).

**Open concern (review 2026-09-29), to explore until we align:** this issue is what makes the evaluator gate affordable. Until it lands, the gate's cost and latency stay visible on the savings dashboard. The agreement bar in 074 E-2 decides how much of the judge's cost can go. Explore whether one encoder can serve several transformers, or each task needs its own.

## What

- A cross-encoder that takes an original text and its simplified version and predicts `facts_kept` as a score from 0 to 1.
- Candidates: ModernBERT (the default) and DeBERTa-v3.
- Training data: an exported golden set version, with human labels from the review UI and judge scores. Rejected records and fallback cases give examples of dropped facts.
- Suggested: synthetic pairs with one fact removed, so the fail class is not too rare.
- The `train` split is for training and `test` for model selection. `holdout` is kept for 074 E-2 only.
- Training runs through the S-5 pipeline (config, data version, seed), logged in MLflow, with compute logged (034 C-8).
- Suggested: score calibration, so the 0.95 threshold means the same as it does for the judge.
- The chosen model is registered in the MLflow model registry as a candidate and packaged to run on CPU.

## Out of scope

- The agreement test against human labels (074 E-2).
- Serving it and swapping it into the harness (075 E-3).
- Evaluators for other tasks.

## Acceptance criteria

- [ ] Both candidates train from one lakeFS version ID with a fixed seed, and a rerun gives the same scores.
- [ ] Each MLflow run links the model to the data version ID, seed, scores, and training compute.
- [ ] No `holdout` record is used in training or selection, checked by record IDs.
- [ ] A selection report compares the candidates on the `test` split: accuracy, recall on the fail class, and calibration at 0.95.
- [ ] The chosen model scores one pair on CPU with p95 latency under 100 ms (suggested target).
- [ ] The chosen model is in the MLflow model registry as a candidate.

## Dependencies

- Depends on: [033 S-5](033-S-5-training-pipeline.md), [065 D-2](065-D-2-review-ui.md), [070 D-4](070-D-4-export-formats.md)
- Blocks: [074 E-2](074-E-2-evaluator-agreement-test.md)

## References

- Epic story: [E-1 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i)
- Backlog plan: [000-plan.md](000-plan.md)
