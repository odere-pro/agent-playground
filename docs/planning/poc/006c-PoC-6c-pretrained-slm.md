---
title: "PoC-6c: Agent pods on a pre-trained SLM"
labels: ["poc", "priority:P0", "area:slm", "area:router", "type:decision"]
milestone: "Agent MVP"
index: 6
iteration: PoC-6c
timebox: "suggested: 1 week, after PoC-6a"
depends_on: ["PoC-6a"]
backlog_refs: ["003 G-1b", "036 S-7", "063 S-8", "028 S-3"]
---

## Question

Can the agent pods run their tasks on a pre-trained SLM, served once behind LiteLLM, with no change to the workload or the chassis? Which engines still do the work on it, and at what cost in quality, tokens, and latency next to the big model?

## Why

The platform exists to move work from big models to small ones. PoC-6a and PoC-6b score the engines on a hosted big model only (user decision, 2026-10-09). This iteration runs the same engines on an SLM.

The agent containers do not care where a model runs. They call the chassis model proxy, and LiteLLM routes the call. So the SLM is one more route, and the agent pods scale apart from the model server. The result tells PoC-9 which engines the template can offer on an SLM. It also tells the SLM waves ([036 S-7](../issues/036-S-7-serve-with-vllm.md), [063 S-8](../issues/063-S-8-tool-call-finetune.md)) what a base model already does without fine-tuning.

It is a separate iteration so the bake-off is not held up by model serving. It needs only the engines and the benchmark kit from PoC-6a. It runs on Docker Compose with a model server, not on the kind cluster, so it can run next to PoC-6b.

## Scope

- [ ] One pre-trained SLM, not fine-tuned: Qwen3-1.7B, the epic's default student SLM ([I](../slm-agent-platform-epic-v3.md#app-i)). It is served once by the llama.cpp OpenAI-compatible server, with native tool calls on (user decision, 2026-10-09; vLLM on a GPU stays with 036 S-7). suggested: the Q8_0 GGUF, with the server on the host so it can use the GPU, reached by LiteLLM in Compose.
- [ ] The model server sits behind LiteLLM on the existing `local-small` route. It runs outside the agent pods. No agent pod holds a model address or a key.
- [ ] Every engine scored in PoC-6a runs both benchmark tasks on `local-small`: plain Python, PydanticAI, LangGraph, OpenAI Agents SDK, and the TypeScript agent. The only change is `spec.model.route`, through the PoC-4 config reload. No image, workload, or chassis change.
- [ ] Task checks with no judge model. The simplifier keeps the facts listed with each input (suggested: numbers and names, matched as strings; the real `facts_kept` metric is [028 S-3](../issues/028-S-3-simplifier-metrics.md)). The lookup calls the right tool, with arguments valid against its schema, and its answer holds the looked-up value.
- [ ] Per engine on the SLM, next to its PoC-6a numbers on the big model: task success, tool-call success, token overhead against plain Python, latency p50 and p95, and time to first token. The share of failed runs is the share that would need the big-model fallback; compare it with the epic's target ([success metrics](../slm-agent-platform-epic-v3.md#success-metrics)).
- [ ] Per engine: the request features the llama.cpp path through LiteLLM rejects or ignores (for example `tool_choice`, `parallel_tool_calls`, or `stream_options`).
- [ ] Scale: N agent pairs share one model server (suggested: N = 1, 2, 4, on the PoC-4 scale stack). Record where throughput stops growing, and whether the agent pods or the model server is the limit.
- [ ] An SLM section in the bake-off ADR from PoC-6.

## Reuse

- **Use:** the PoC-6a benchmark kit and scorecard, the PoC-4 scale stack, load matrix, and config reload, the `local-small` route from PoC-1, and the llama.cpp server.
- **Build:** the `local-small` route to the model server (a LiteLLM config change), the task checks, and the SLM rows of the scorecard.
- Details: [reuse analysis](010-reuse-analysis.md)

## Out of scope

- Fine-tuning, LoRA, and training (033 S-5, 035 S-6, 063 S-8).
- vLLM on a GPU, guided decoding as a service, KServe, and canary rollout (036 S-7, 039 X-2, 048 M-4).
- Quantized variants beyond the one model file (049 M-5).
- The fallback itself, retries between routes, and the evaluator gate (017 H-4, PoC-7). This iteration only counts the runs that would need a fallback.
- The `remote`-lane engines (Claude Agent SDK, smolagents). Their lane runs on the kind cluster, and their model calls take the same chassis proxy. suggested: add one only if the bake-off ADR makes it the default.
- The route's name. `local-small` stays; renaming it to `simplifier-slm` is open in 003 G-1b.
- A second SLM. suggested: Gemma 3 1B, the epic's second candidate, only if Qwen3-1.7B fails every engine.

## Demo

The same `/v1/run` call on each engine, first on `big-default`, then on `local-small` after a config reload, side by side. Then the scorecard's SLM rows, and the scale run.

## Exit criteria

- [ ] Every PoC-6a engine runs both tasks on the pre-trained SLM through the chassis model proxy and LiteLLM, and only `spec.model.route` changed.
- [ ] Per engine, task success, tool-call success, the share of runs that would need a fallback, token overhead against plain Python, and latency are recorded on the SLM, next to the big-model numbers from PoC-6a.
- [ ] Per engine, the request features the SLM route rejects or ignores are listed.
- [ ] The scale run shows where throughput stops growing as agent pairs share one model server, and what limits it.
- [ ] The model, its file hash, the runtime version, and the server flags are pinned and recorded, so every run can be repeated.
- [ ] The bake-off ADR has an SLM section. If the default engine fails on the SLM, the default is reopened.

## Links

- Plan: [000-plan.md](000-plan.md) · Previous: [PoC-6](006-PoC-6-framework-bake-off.md), part A · Next: [PoC-7](007-PoC-7-cross-cutting-decisions.md)
- Backlog issues this informs: [003 G-1b](../issues/003-G-1b-named-model-routes.md), [036 S-7](../issues/036-S-7-serve-with-vllm.md), [063 S-8](../issues/063-S-8-tool-call-finetune.md), [028 S-3](../issues/028-S-3-simplifier-metrics.md)
- Epic: [B.3](../slm-agent-platform-epic-v3.md#b3), [I](../slm-agent-platform-epic-v3.md#app-i), [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
