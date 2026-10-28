---
title: "S-3: Simplifier metrics: tokens, readability, semantic similarity, facts_kept, banned words"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:slm", "size:M", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 28
epic_id: S-3
depends_on: ["027 S-1"]
blocks: ["029 S-2", "037 M-2", "042 A-2"]
epic_refs: [I, success-metrics]
---

## Why

The metrics decide what "good" means for every pair, model, and release. They are placed before data generation so every generated pair can be scored. The same metrics feed the eval gate (037 M-2) and the agent's `facts_kept` gate (042 A-2), against the success target `facts_kept` ≥ 0.95.

## What

- A metrics package that scores one (original, simplified) pair or a whole JSONL set.
- Tokens: the count before and after, and the reduction ratio.
- Readability: a readability score (suggested: a standard formula such as Flesch reading ease).
- Semantic similarity: cosine similarity of embeddings from an open embedding model in Appendix I (for example Qwen3-Embedding-0.6B).
- `facts_kept`: the share of facts in the original that are still in the simplified text, from 0 to 1, scored by the open judge model (a large open Qwen3 or Gemma 3 on vLLM) through a named route.
- Banned words: the count of words from the versioned 027 S-1 list.
- Judge and embedding calls at temperature 0, with model and route versions recorded in the output.
- Per-pair and aggregate results as JSON, ready to log to MLflow later.
- Status after PoC-6: the bake-off checks are string matches, no judge model: the simplifier output holds `2026`, `Acme`, and `30`; the lookup answer holds `small language model` and `retrieval-augmented generation` (`packages/bakeoff/src/bakeoff/tasks.py`). They stand in for `facts_kept`; the real metric is still this issue's.

## Out of scope

- The CI eval gate (037 M-2).
- The harness evaluator gate for the simplifier (042 A-2).
- The evaluator SLM that later replaces the judge (073 E-1, 075 E-3).

## Acceptance criteria

- [ ] One command scores a JSONL file of pairs and writes per-pair and aggregate results for all five metrics.
- [ ] `facts_kept` is 1.0 for an identical pair and drops for a pair with a removed number or name (unit tests with fixed fixtures).
- [ ] Banned-word counts use the 027 S-1 list, and the list version is in the output.
- [ ] Scoring the same input twice gives the same scores.
- [ ] `facts_kept` is checked against a hand-labeled sample (suggested: 50 pairs), and the agreement is reported.
- [ ] Judge and embedding models are called by route name, and their versions are in the output.

## Dependencies

- Depends on: [027 S-1](027-S-1-rewrite-rules.md)
- Blocks: [029 S-2](029-S-2-training-data-generation.md), [037 M-2](037-M-2-eval-gate-ci.md), [042 A-2](042-A-2-evaluator-gate-fallback.md)

## References

- Epic story: [S-3 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
