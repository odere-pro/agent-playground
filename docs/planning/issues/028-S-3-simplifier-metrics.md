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

- [ ] One command scores a golden case file and writes per-case and aggregate results for `facts_kept` and banned words.
- [ ] `facts_kept` is 1.0 for an identical pair and drops for a pair with a removed number or name (unit tests with fixed fixtures).
- [ ] Banned words use the case's `banned:` list.
- [ ] Scoring the same input twice gives the same scores.
- [ ] The judge's `facts_kept` agrees with the 50 hand labels of the gate set, and the agreement is reported from the Mac run. The label of g08 is settled first.
- [ ] The judge route and the rubric version are in the report. (`packages/chassis/tests/test_evaluators.py` `test_judge_reports_usage_cost_and_route` covers the route, not the rubric version.)

## Dependencies

- Depends on: none
- Blocks: [029 S-2](029-S-2-training-data-generation.md), [037 M-2](037-M-2-eval-gate-ci.md), [042 A-2](042-A-2-evaluator-gate-fallback.md), [064 D-1](064-D-1-golden-set-agent.md), [137 CH-17](137-CH-17-judge-calibration.md)

## References

- Epic story: [S-3 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
