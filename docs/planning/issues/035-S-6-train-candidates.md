---
title: "S-6: Train candidates on Qwen3, Gemma 3, and Llama 3.2, and pick the best"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:slm", "size:L", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 35
epic_id: S-6
depends_on: ["031 G-5", "033 S-5"]
blocks: ["036 S-7"]
epic_refs: [I]
---

## Why

The best small base model is found by testing, not by guessing. This issue trains candidates on Qwen3, Gemma 3, and Llama 3.2 and picks the one that meets the targets at the lowest serving cost. It waits for the break-even check (031 G-5) because it is the first real GPU spend.

## What

- Candidates from Appendix I: Qwen3 0.6B–1.7B, Gemma 3 270M–4B, and Llama 3.2 1B–3B. The defaults are Qwen3-1.7B first and Gemma 3 1B second.
- The same data version, rules version, and pipeline (033 S-5) for every candidate, with a small hyperparameter sweep each (suggested).
- Every candidate scored on the held-out test set with the S-3 metrics.
- Latency and throughput measured for each candidate on the same GPU.
- A selection rule: meet `facts_kept` ≥ 0.95 and the other targets first, then pick the lowest latency and serving cost.
- The winner registered in the MLflow model registry, with a short comparison report.
- If no candidate meets the targets, a report of the gap and next steps (for example more data or a bigger base).

## Out of scope

- Serving the winner with vLLM (036 S-7).
- The CI eval gate (037 M-2).
- Quantized variants (049 M-5).

## Acceptance criteria

- [ ] At least one candidate from each of Qwen3, Gemma 3, and Llama 3.2 is trained with the same data version and pipeline.
- [ ] Every candidate is scored on the held-out test set (at least 300 records), and the scores are in MLflow.
- [ ] The chosen model has `facts_kept` ≥ 0.95 on the held-out set and meets the other targets, or the gap is reported with a plan.
- [ ] Latency and throughput per candidate are measured on the same GPU and added to the 031 G-5 cost model.
- [ ] The chosen model is registered in MLflow, with the comparison report linked.
- [ ] Every candidate run passes the GPAI compute check (034 C-8).

## Dependencies

- Depends on: [031 G-5](031-G-5-cost-model-break-even.md), [033 S-5](033-S-5-training-pipeline.md)
- Blocks: [036 S-7](036-S-7-serve-with-vllm.md)

## References

- Epic story: [S-6 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i)
- Backlog plan: [000-plan.md](000-plan.md)
