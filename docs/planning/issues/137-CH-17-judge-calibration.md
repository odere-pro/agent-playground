---
title: "CH-17: Judge calibration against people: fail cases, a second labeler, TPR and TNR"
labels: ["story", "priority:P0", "phase:3-simplifier-agent", "area:evaluator", "size:M", "layer:agent-profile"]
milestone: "W6 Golden set and evaluator SLM"
index: 137
epic_id: CH-17
depends_on: ["028 S-3", "042 A-2"]
blocks: []
epic_refs: [B.2, E.5]
---

## Why

The judge has not been calibrated against people. The gate set has 10 `fail` cases of 50, written by one author with one reviewer pass and no second label (`packages/eval-kit/src/eval_kit/golden/cases-gate.yaml`). The vault note `wiki/evals/Eval graders.md`, section "Calibration (before a judge can block a release)", bars an uncalibrated judge from blocking, and asks for a floor of 30 `fail` cases. The [product plan's gap review](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing) (2026-11-07) makes this the second step of phase B: it blocks the judge "on by default".

## What

- 20 to 40 more `fail` cases in `cases-gate.yaml`, so the set holds at least 30.
- A second person labels 20% of the set, and Cohen's kappa is recorded.
- The judge's TPR and TNR against the labels, at or above 0.85 (suggested), before `sync` gates anywhere. Until then the gate runs `async` only.
- After the first Mac run, error analysis on real inputs: cases per failure type, each with a `source` field.

## Out of scope

- The rubric's shape: pass, fail, or unknown, reasoning first (P-31 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md)).
- The judge's agreement on the 50 labels from the Mac run (028 S-3).
- The evaluator SLM (073 E-1, parked).

## Acceptance criteria

- [ ] The gate set holds at least 30 `fail` cases, each with a reason.
- [ ] A second person labels 20% of the gate set, and Cohen's kappa is recorded in a dated note in `pocs/poc-07-cross-cutting-decisions/notes/`.
- [ ] The judge's TPR and TNR against the labels are recorded from the Mac run. Both are at or above 0.85 (suggested), or the shortfall and the next step are written down.
- [ ] Until both are met, no profile sets `evaluator_mode: sync`, and a profile test holds it.
- [ ] After the first Mac run, the cases added per failure type carry `source`, and `golden/` stays the only source of cases.

## Dependencies

- Depends on: [028 S-3](028-S-3-simplifier-metrics.md), [042 A-2](042-A-2-evaluator-gate-fallback.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2) · [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
