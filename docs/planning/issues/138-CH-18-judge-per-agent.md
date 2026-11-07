---
title: "CH-18: The judge per agent: rubric by name, escapes on the trace, a shadow judge"
labels: ["story", "priority:P1", "phase:3-simplifier-agent", "area:evaluator", "size:M", "layer:chassis", "layer:agent-profile"]
milestone: "W6 Golden set and evaluator SLM"
index: 138
epic_id: CH-18
depends_on: ["042 A-2"]
blocks: []
epic_refs: [B.2, G.2]
---

## Why

One module-constant rubric judges every agent and every task, with no code check first. "Escapes" needs a per-run join of two counters. "First-attempt pass rate" reads 100% in `async` mode, which never retries. One evaluator runs, with no sampled second judge to catch drift (ADR-010 item 10). The [product plan's gap review](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing) (2026-11-07) puts these in the second step of phase B.

## What

- `spec.harness.rubric`: a rubric by name, version pinned, per agent.
- The judge is skipped when the answer is structured: `end.output` with no `text`.
- The gate score is written through `FeedbackPort` as a score named after the rubric version, so escapes are one query on the trace.
- [metrics](../../specs/metrics.md) gives first-attempt pass rate as `1 − evaluator_calls{outcome="fail"} / evaluator_calls{outcome in pass, fail}`.
- `spec.harness.shadow_evaluator` and `shadow_sample_rate`: a second judge scores a sample, only the main judge gates, and agreement is counted as `evaluator_agreement{pair, agree}`.

## Out of scope

- The judge's tokens and cost in `/metrics`: 042 A-2 and P-14 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md).
- The evaluator SLM as the second judge (075 E-3, parked).
- Calibration against people (137 CH-17).

## Acceptance criteria

- [ ] `spec.harness.rubric` names a rubric and its version, an unknown name is refused at load, and the judge uses the named rubric in a test with the fake model.
- [ ] An answer with `end.output` and no `text` makes no judge call and counts as skipped (suggested: `evaluator_calls{outcome="skipped"}`).
- [ ] The gate score reaches the fake `FeedbackPort` as a score named after the rubric version, and a test reads it back.
- [ ] `docs/specs/metrics.md` gives the first-attempt pass rate formula above.
- [ ] With `shadow_evaluator` set and `shadow_sample_rate: 1.0`, both judges score each attempt, only the main one gates, and `evaluator_agreement{pair, agree}` counts the result.

## Dependencies

- Depends on: [042 A-2](042-A-2-evaluator-gate-fallback.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2) · [G.2](../slm-agent-platform-epic-v3.md#g2)
- Backlog plan: [000-plan.md](000-plan.md)
