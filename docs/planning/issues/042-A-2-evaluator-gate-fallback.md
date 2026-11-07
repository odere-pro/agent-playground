---
title: "A-2: Evaluator gate on facts_kept, one retry, then fallback to a big model"
labels: ["story", "priority:P0", "phase:3-simplifier-agent", "area:agent", "size:M", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 42
epic_id: A-2
depends_on: ["017 H-4", "028 S-3"]
blocks: ["046 A-5", "047 M-3", "064 D-1", "075 E-3", "126 L-2", "137 CH-17", "138 CH-18"]
epic_refs: [B.2]
---

## Why

The top quality risk is that an agent drops facts. This issue guards answers with a `facts_kept` check by the judge, one retry, and a fallback to the configured route. Phase B turns the judge on by default, so its route, defaults, outage rule, and cost must be settled here. The judge's cost and the recommended defaults are in [ADR-010](../adr/010-evals-offline-tool-online-gate.md) items 9 and 10.

## What

- Decided in [ADR-010](../adr/010-evals-offline-tool-online-gate.md) (accepted): the gate wraps the engine in the chassis, in every lane, with sync and async modes and a sample rate per request id. `judge` (through `ModelPort`) and `encoder` (an HTTP classifier) are the two in-chassis evaluator adapters, picked by `spec.adapters.evaluator`. There is no evaluator agent and no A2A evaluator adapter.
- The flow as built: score the answer; below `threshold`, retry once on the same route; still below, one unscored attempt on `fallback_route`, end status `fallback`. With no fallback route, the end is `error` `evaluator_gate_failed`. A retry that passes ends `ok`.
- `threshold`: built default 0.7. suggested: 0.95 for the facts rubric (a rubric value, not a platform value).
- An evaluator outage fails open as built (ADR-010 item 4), decided 2026-11-06 (P-1).
- The judge's route is `spec.model.judge_route`, from the route map (012 H-3).
- Phase B defaults for the `cloud` profile: `evaluator_gate: true`, `evaluator_mode: async`, `evaluator_sample_rate: 0.1`, `spec.adapters.evaluator: judge` (ADR-010 item 10).
- The fallback rate bar, < 10%, is the counter-metric target in `docs/specs/metrics.md`.
- For the DEC-1 decision pack: the default mode and sample rate (ADR-010 question 2), and where the judge score goes (Langfuse scores for async results are not written yet).

## Out of scope

- The gate, retry, and fallback mechanics in the chassis (017 H-4). This issue configures and uses them.
- The container dashboard (046 A-5).
- Sending low scores to human review (064 D-1, 065 D-2).
- The evaluator SLM (073 E-1) and the swap to it (075 E-3).

## Acceptance criteria

- [x] An answer that scores at or above the configured `threshold` returns `status: ok` after one model call. · Delivered in PoC-7: `packages/chassis/tests/test_evaluator_gate.py::test_a_passing_answer_goes_out_once_with_the_judge_metrics` (status ok, one attempt, one model call)
- [x] A fixture that drops a fact triggers exactly one retry and then an answer from the configured `fallback_route`, with `status: fallback`. · Delivered in PoC-7, PoC-8: `packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back`, `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_retries_once_then_falls_back`, `pocs/poc-08-cross-cutting-build/tests/test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route` (every lane; end status fallback)
- [x] No call makes more than one retry. · Delivered in PoC-7: `packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back` (retries: 1 gives two scored attempts, then the unscored fallback; `GATE_RETRIES == 1`)
- [ ] The gate calls only the configured evaluator adapter (`spec.adapters.evaluator`).
- [x] An evaluator outage fails open: the attempt ships unscored and counts as `chassis.gate_failed_open`. · Delivered in PoC-7: `packages/chassis/tests/test_evaluator_gate.py::test_an_evaluator_outage_fails_open` and `::test_gate_sends_the_retry_answer_and_fails_open_when_the_evaluator_fails_on_it`
- [ ] On the 30-case gate set (`packages/eval-kit/src/eval_kit/golden/cases-gate.yaml`), the fallback rate is < 10%.
- [ ] Pass, retry, and fallback counts show in `/metrics` and in the router's per-agent counts.
- [x] The judge's route comes from the route map: `spec.model.judge_route`, read by `JudgeEvaluator` on every call, and reloadable. · Delivered by: PR #78, `packages/chassis/tests/test_model_routes.py::test_the_gate_scores_on_spec_model_judge_route_else_the_judges_own` and `::test_the_map_and_the_judge_route_are_reloadable`; `packages/chassis/tests/test_config_loader.py::test_a_reload_changes_the_route_map_and_the_judge_route`
- [ ] The judge's route is checked against the service's key: a judge route the key does not allow is refused.
- [ ] The `cloud` profile sets `evaluator_gate: true`, `evaluator_mode: async`, `evaluator_sample_rate: 0.1`, and `spec.adapters.evaluator: judge`, with a test per setting.
- [ ] The judge's tokens and cost count in `chassis_model_tokens_total` and `chassis_model_cost_microusd_total` under the judge route, or in a named judge family, and a scrape test shows it.
- [ ] The judge's latency, cost, and agreement from the Mac run are recorded in `pocs/poc-07-cross-cutting-decisions/notes/`, and ADR-010 item 10 is confirmed or changed.

## Dependencies

- Depends on: [017 H-4](017-H-4-harness-features.md), [028 S-3](028-S-3-simplifier-metrics.md)
- Blocks: [046 A-5](046-A-5-agent-dashboard.md), [047 M-3](047-M-3-shadow-mode.md), [064 D-1](064-D-1-golden-set-agent.md), [075 E-3](075-E-3-evaluator-replaces-judge.md), [126 L-2](126-L-2-system-prompt-improver.md), [137 CH-17](137-CH-17-judge-calibration.md), [138 CH-18](138-CH-18-judge-per-agent.md)

## References

- Epic story: [A-2 in Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2)
- Backlog plan: [000-plan.md](000-plan.md)
