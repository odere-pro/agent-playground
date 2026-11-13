---
title: "S-3: `facts_kept` and banned-word scoring for the eval gate and the judge"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:slm", "size:S", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 28
epic_id: S-3
depends_on: []
blocks: ["029 S-2", "037 M-2", "042 A-2", "064 D-1", "137 CH-17"]
epic_refs: [I, success-metrics]
---

## Why

The score decides what "good" means for an answer. The SLM data track that used it to filter training pairs is parked, so the live half is the score the gate and the judge use. The same score feed the eval gate (037 M-2) and the agent's `facts_kept` gate (042 A-2), against the success target `facts_kept` ≥ 0.95.

## What

- A scoring module that scores one (input, answer) pair or a whole golden case file.
- `facts_kept`: the share of facts in the original that are still in the answer, from 0 to 1, scored by the judge (`JudgeEvaluator`, rubric `judge-facts-v1`) through a named route.
- Banned words: the count of words from the case's `banned:` list in the golden set.
- Judge calls at temperature 0, with the judge route and the rubric version recorded in the output.
- Per-case and aggregate results as JSON.

## Out of scope

- The CI eval gate (037 M-2).
- The harness evaluator gate for the simplifier (042 A-2).
- The evaluator SLM that later replaces the judge (073 E-1, 075 E-3).
- Tokens before and after, readability, and semantic similarity of embeddings: parked with the SLM track. They filter and rank SLM training pairs; no phase needs them.
- The bake-off string matches (`packages/bakeoff/src/bakeoff/tasks.py`) stand in for `facts_kept` in the bake-off only. They are not the score this issue builds (`pocs/poc-06a-bake-off-sidecar-lane/notes/backlog-changes.md`).

## Acceptance criteria

- [x] One command scores a golden case file and writes per-case and aggregate results for `facts_kept` and banned words. · Delivered by: `uv run python -m eval_kit.simplifier_metrics --cases <file> --answers <json> --judge offline|litellm --out <json>` (`simplifier-metrics.v0`); `packages/eval-kit/tests/test_eval_kit_simplifier_metrics.py::test_command_scores_a_case_file_and_writes_per_case_and_aggregate`, `::test_command_defaults_to_the_50_gate_cases`
- [x] `facts_kept` is 1.0 for an identical pair and drops for a pair with a removed number or name (unit tests with fixed fixtures). · Delivered by: `packages/eval-kit/tests/test_eval_kit_simplifier_metrics.py::test_facts_kept_is_one_for_an_identical_pair`, `::test_facts_kept_drops_when_a_number_is_removed`, `::test_facts_kept_drops_when_a_name_is_removed`, `::test_a_changed_number_is_not_kept`; offline, through `JudgeEvaluator` on the deterministic stand-in `FactsModel`, not the real judge
- [x] Banned words use the case's `banned:` list. · Delivered by: `packages/eval-kit/tests/test_eval_kit_simplifier_metrics.py::test_banned_words_use_the_case_banned_list`, `::test_banned_hits_is_case_insensitive`
- [x] Scoring the same input twice gives the same scores. · Delivered by: `packages/eval-kit/tests/test_eval_kit_simplifier_metrics.py::test_scoring_the_same_input_twice_gives_the_same_scores`; the command writes byte-identical reports (`::test_command_scores_a_case_file_and_writes_per_case_and_aggregate`). Offline judge only; the real judge runs at temperature 0
- [ ] The judge's `facts_kept` agrees with the 50 hand labels of the gate set, and the agreement is reported from the Mac run. The label of g08 is settled first.
- [x] The judge route and the rubric version are in the report. (`packages/chassis/tests/test_evaluators.py` `test_judge_reports_usage_cost_and_route` covers the route, not the rubric version.) · Delivered by: `packages/eval-kit/tests/test_eval_kit_simplifier_metrics.py::test_report_names_the_judge_route_and_the_rubric_version` (`judge.route`, `judge.rubric_version` `judge-facts-v1`, `judge.temperature` 0)

## Dependencies

- Depends on: none
- Blocks: [029 S-2](029-S-2-training-data-generation.md), [037 M-2](037-M-2-eval-gate-ci.md), [042 A-2](042-A-2-evaluator-gate-fallback.md), [064 D-1](064-D-1-golden-set-agent.md), [137 CH-17](137-CH-17-judge-calibration.md)

## References

- Epic story: [S-3 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
