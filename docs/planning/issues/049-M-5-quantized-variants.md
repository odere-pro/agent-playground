---
title: "M-5: Quantized 4-bit variants for CPU and edge, each with its own eval"
labels: ["story", "priority:P2", "phase:2-simplifier-slm", "area:lifecycle", "size:M", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 49
epic_id: M-5
depends_on: ["037 M-2"]
blocks: []
epic_refs: [I]
---

## Why

A 4-bit model can run on CPU or small edge devices, which cuts GPU cost where the quality holds. Each variant needs its own eval, because quantization can drop facts. It is P2: no success metric depends on it.

## What

- Build 4-bit variants of the promoted simplifier SLM. Suggested: GGUF 4-bit for llama.cpp on CPU, and AWQ 4-bit for vLLM on GPU.
- A build script in the training pipeline (033 S-5), with a fixed seed and calibration set, so each variant is reproducible from a model version.
- Each variant is its own MLflow model version, linked to its parent model and data version.
- Each variant passes the eval gate (037 M-2) on its own, with the same `facts_kept` ≥ 0.95 target.
- Each variant gets its own named route (suggested: `simplifier-slm-q4`), so it is swapped in by router config only.
- Local runs use the direct llama.cpp adapter (012 H-3).
- A report compares each variant with the full model.

## Out of scope

- Canary rollout of a variant; it reuses the flow from 048 M-4.
- Packaging for specific edge devices.
- Running the evaluator SLM on CPU (073 E-1).

## Acceptance criteria

- [ ] At least one 4-bit variant builds from a model version with one command, and a rebuild gives the same eval scores.
- [ ] Each variant is in MLflow, linked to its parent model and data version.
- [ ] A variant below `facts_kept` 0.95 on the golden set fails the eval gate and is not promoted.
- [ ] A variant runs on CPU with llama.cpp and answers through its named route.
- [ ] The report lists `facts_kept`, p95 latency on CPU, memory use, and cost per 1,000 requests for each variant and the full model.

## Dependencies

- Depends on: [037 M-2](037-M-2-eval-gate-ci.md)
- Blocks: none

## References

- Epic story: [M-5 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i)
- Backlog plan: [000-plan.md](000-plan.md)
