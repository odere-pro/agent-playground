---
title: "S-8: Optional: fine-tune an SLM on tool-call examples for tool agents"
labels: ["story", "priority:P2", "phase:2-simplifier-slm", "area:slm", "size:L", "layer:platform"]
milestone: "W5 Chassis completion"
index: 63
epic_id: S-8
depends_on: ["033 S-5", "054 H-16"]
blocks: []
epic_refs: [B.3, I]
---

## Why

Tool agents need a model that picks the right tool and fills its arguments well. Small models do this less reliably than big ones, and fine-tuning on tool-call examples closes part of the gap. It is optional (P2): tool agents can run on a base Qwen3 model with a big-model fallback route. It needs the training pipeline (033 S-5) and the tool port (054 H-16).

## What

- A tool-call dataset: task, tool schemas, the expected tool calls (or no call), and the final answer. Generated with an open-weight teacher, with `source` on every example, plus a hand-checked test split.
- Base model: a Qwen3 SLM with native tool calling on vLLM, as in the model catalog.
- LoRA training through the S-5 pipeline, with config, data version, and seed, logged in MLflow.
- Training compute logged per run (034 C-8).
- Its own eval: correct tool, arguments valid against the tool schema, argument match, and correct "no tool" cases. Native mode and guided JSON mode are both scored.
- Served on a named route that declares native tool support, with a big-model fallback route.
- A go or no-go note on whether tool agents should use it.
- Status after PoC-6: PoC-6c measures what the pre-trained base model does on the bake-off tasks without fine-tuning. This is the baseline this issue improves on. No number exists yet; it waits on the Mac run.

## Out of scope

- The tool port itself (054 H-16).
- Registry search, which tool agents will call later (091 R-11).
- The router SLM (127 L-3).

## Acceptance criteria

- [ ] A versioned tool-call dataset exists, with a held-out test split that is never used in training.
- [ ] The fine-tuned model and the base model are both scored on the test split with the listed metrics.
- [ ] The fine-tuned model beats the base model on correct-tool rate, or the go or no-go note says why not.
- [ ] Each run in MLflow links to the data version, the seed, the scores, and the training compute.
- [ ] A test tool agent calls tools through the tool port on the fine-tuned route, and falls back to the big-model route on a failed call.

## Dependencies

- Depends on: [033 S-5](033-S-5-training-pipeline.md), [054 H-16](054-H-16-tool-port.md)
- Blocks: none

## References

- Epic story: [S-8 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [B.3](../slm-agent-platform-epic-v3.md#b3) · [I](../slm-agent-platform-epic-v3.md#app-i)
- Backlog plan: [000-plan.md](000-plan.md)
