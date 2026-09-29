---
title: "S-2: Training data generation: 2,000 to 5,000 pairs with an open-weight teacher"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:slm", "size:M", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 29
epic_id: S-2
depends_on: ["003 G-1b", "027 S-1", "028 S-3"]
blocks: ["030 S-4"]
epic_refs: [I, R1, principles]
---

## Why

The SLM learns from 2,000–5,000 pairs built from real outputs. An open-weight teacher that the team runs itself is the default, so no provider terms restrict the data; this is an epic principle and the mitigation for a listed risk. Every pair is scored with the 028 S-3 metrics as it is made.

## What

- Source texts from real big-model outputs (an epic dependency, with the owner named in 001 DEC-1), with PII removed first. suggested: use the same redaction library as the harness security middleware (022 H-6), called from a script, so data work can start during W2 without waiting for the middleware.
- A teacher: a large open Qwen3 or Gemma 3 model on vLLM, called through a named route (003 G-1b), with the 027 S-1 system prompt.
- A hosted teacher only after its terms are checked [R1], with the check recorded.
- Every pair records `source` (which model produced the original), the teacher route and version, and the rules version.
- Every pair scored with the S-3 metrics. Pairs below the thresholds are dropped (`facts_kept` < 0.95 or any banned word).
- Near-duplicates removed, and the set balanced by length and topic.
- The dataset stored in object storage as JSONL with an immutable data version ID (suggested: a content hash, until lakeFS in 069 D-7).
- A generation config and script that re-run the same job.

## Reuse

- **Use:** vLLM offline batch inference with the open-weight teacher, and HF datasets for storage. Optional: distilabel pipelines (now community-maintained). Presidio for PII removal.
- **Build:** the teacher prompts, and the filters that use the S-3 metrics.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Hand review and the held-out test set (030 S-4).
- Tool-call training data (063 S-8).
- Versioned datasets in lakeFS (069 D-7).

## Acceptance criteria

- [ ] Between 2,000 and 5,000 pairs are kept after filtering, each built from a real output.
- [ ] Every pair has `source`, the teacher route and version, the rules version, and all S-3 scores.
- [ ] Every kept pair has `facts_kept` ≥ 0.95 and no banned words.
- [ ] The teacher is an open-weight model run by the team, or a hosted teacher's terms check is linked.
- [ ] A PII scan of the final set finds no emails or phone numbers.
- [ ] The dataset has an immutable data version ID, and the script re-runs from the saved config.

## Dependencies

- Depends on: [003 G-1b](003-G-1b-named-model-routes.md), [027 S-1](027-S-1-rewrite-rules.md), [028 S-3](028-S-3-simplifier-metrics.md)
- Blocks: [030 S-4](030-S-4-hand-review-test-set.md)

## References

- Epic story: [S-2 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i) · [R1](../slm-agent-platform-epic-v3.md#r1) · [Principles](../slm-agent-platform-epic-v3.md#principles)
- Backlog plan: [000-plan.md](000-plan.md)
