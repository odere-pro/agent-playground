# PoC-6c: Agent pods on a pre-trained SLM

Status: not started
Planning doc: [006c-PoC-6c-pretrained-slm.md](../../docs/planning/poc/006c-PoC-6c-pretrained-slm.md)
Time box: suggested: 1 week, after PoC-6a

## Question

Can the agent pods run their tasks on a pre-trained SLM, served once behind LiteLLM, with no change to the workload or the chassis? Which engines still do the work on it, and at what cost in quality, tokens, and latency next to the big model?

## Scope

- [ ] One pre-trained SLM, not fine-tuned: Qwen3-1.7B, the epic's default student SLM ([I](../../docs/planning/slm-agent-platform-epic-v3.md#app-i)). It is served once by the llama.cpp OpenAI-compatible server, with native tool calls on (user decision, 2026-10-09; vLLM on a GPU stays with 036 S-7). suggested: the Q8_0 GGUF, with the server on the host so it can use the GPU, reached by LiteLLM in Compose.
- [ ] The model server sits behind LiteLLM on the existing `local-small` route. It runs outside the agent pods. No agent pod holds a model address or a key.
- [ ] Every engine scored in PoC-6a runs both benchmark tasks on `local-small`: plain Python, PydanticAI, LangGraph, OpenAI Agents SDK, and the TypeScript agent. The only change is `spec.model.route`, through the PoC-4 config reload. No image, workload, or chassis change.
- [ ] Task checks with no judge model. The simplifier keeps the facts listed with each input (suggested: numbers and names, matched as strings; the real `facts_kept` metric is [028 S-3](../../docs/planning/issues/028-S-3-simplifier-metrics.md)). The lookup calls the right tool, with arguments valid against its schema, and its answer holds the looked-up value.
- [ ] Per engine on the SLM, next to its PoC-6a numbers on the big model: task success, tool-call success, token overhead against plain Python, latency p50 and p95, and time to first token. The share of failed runs is the share that would need the big-model fallback; compare it with the epic's target ([success metrics](../../docs/planning/slm-agent-platform-epic-v3.md#success-metrics)).
- [ ] Per engine: the request features the llama.cpp path through LiteLLM rejects or ignores (for example `tool_choice`, `parallel_tool_calls`, or `stream_options`).
- [ ] Scale: N agent pairs share one model server (suggested: N = 1, 2, 4, on the PoC-4 scale stack). Record where throughput stops growing, and whether the agent pods or the model server is the limit.
- [ ] An SLM section in the bake-off ADR from PoC-6.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] Every PoC-6a engine runs both tasks on the pre-trained SLM through the chassis model proxy and LiteLLM, and only `spec.model.route` changed.
- [ ] Per engine, task success, tool-call success, the share of runs that would need a fallback, token overhead against plain Python, and latency are recorded on the SLM, next to the big-model numbers from PoC-6a.
- [ ] Per engine, the request features the SLM route rejects or ignores are listed.
- [ ] The scale run shows where throughput stops growing as agent pairs share one model server, and what limits it.
- [ ] The model, its file hash, the runtime version, and the server flags are pinned and recorded, so every run can be repeated.
- [ ] The bake-off ADR has an SLM section. If the default engine fails on the SLM, the default is reopened.

## How to run

```bash
make test-poc POC=06c
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
