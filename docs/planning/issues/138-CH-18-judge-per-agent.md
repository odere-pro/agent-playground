---
title: "CH-18: The judge per agent: rubric by name, escapes on the trace, a shadow judge"
labels: ["story", "priority:P1", "phase:0-harness", "area:evaluator", "size:M", "layer:chassis", "layer:agent-profile"]
milestone: "Done: delivered by the PoC track"
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

Delivered by: the structured-answer skip (PR 160) and, on 2026-11-13, the rubric, score feedback, and shadow judge tests named on each box

- [x] `spec.harness.rubric` names a rubric and its version, an unknown name is refused at load, and the judge uses the named rubric in a test with the fake model. · Delivered by: `packages/chassis/tests/test_evaluator_gate_rubric.py::test_an_unknown_rubric_is_refused_at_load`, `::test_the_judge_sends_the_named_rubric_with_the_fake_model`
- [x] An answer with `end.output` and no `text` makes no judge call and counts as skipped (suggested: `evaluator_calls{outcome="skipped"}`). · Delivered by: `packages/chassis/tests/test_evaluator_gate_skip.py::test_a_structured_answer_makes_no_judge_call_and_counts_as_skipped` (sync and async)
- [x] The gate score reaches the fake `FeedbackPort` as a score named after the rubric version, and a test reads it back. · Delivered by: `packages/chassis/tests/test_evaluator_gate_rubric.py::test_the_gate_score_reaches_feedback_named_after_the_rubric_version`, `packages/contract-suites/src/chassis_contracts/feedback.py::test_a_named_score_keeps_its_name` (fake, Langfuse, LangSmith)
- [x] `docs/specs/metrics.md` gives the first-attempt pass rate formula above. · Delivered by: `packages/chassis/tests/test_evaluator_gate_skip.py::test_the_metrics_spec_gives_the_first_attempt_pass_rate_formula`
- [x] With `shadow_evaluator` set and `shadow_sample_rate: 1.0`, both judges score each attempt, only the main one gates, and `evaluator_agreement{pair, agree}` counts the result. · Delivered by: `packages/chassis/tests/test_evaluator_gate_rubric.py::test_a_shadow_judge_scores_every_attempt_but_only_the_main_one_gates`, `::test_the_shadow_scores_each_attempt_of_a_retried_run`

## Dependencies

- Depends on: [042 A-2](042-A-2-evaluator-gate-fallback.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2) · [G.2](../slm-agent-platform-epic-v3.md#g2)
- Backlog plan: [000-plan.md](000-plan.md)
