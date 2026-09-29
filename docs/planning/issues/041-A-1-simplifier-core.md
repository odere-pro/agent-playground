---
title: "A-1: Simplifier business logic as a workload behind the chassis"
labels: ["story", "priority:P0", "phase:3-simplifier-agent", "area:agent", "size:M", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 41
epic_id: A-1
depends_on: ["017 H-4", "025 H-10", "036 S-7", "013 CH-2", "026 CH-4"]
blocks: ["042 A-2", "043 A-3", "047 M-3"]
epic_refs: [Fig.2, B.1]
---

## Why

The simplifier is the first real agent and the source of the first measured savings. It sits on the critical path right after the served SLM (036 S-7), because it needs the chassis features, the template repo, and the `simplifier-slm` route. It also proves the chassis promise: a new agent differs from the echo agent only in business logic.

## What

- A new agent from the template repo (025 H-10): class `stateless`, kind `transformer`, config `agents/simplifier.yaml` in the config store.
- The core `handle(input: TaskInput, ctx: Context)` rewrites text in plain language, using the system prompt from `prompts.system_ref` (rewrite rules and banned words from 027 S-1).
- Model calls go only through the chassis model proxy (013 CH-2) to the `simplifier-slm` route, with `temperature: 0` and `max_tokens: 800`. The workload holds no key.
- Output is JSON, checked against the agent's output schema (the H-4 schema check), with guided decoding on the vLLM route.
- The core returns `start`, `delta`, `metrics`, and `end` events, so every inbound adapter can stream or return a complete response.
- `metrics` carries tokens in and out and latency; `versions` carries the config, prompt, and model route versions.
- No state in the core: same input and same config version give the same output.
- Scope `simplify:run`. Runs in Docker Compose, and on Kubernetes in the `sidecar` lane with `spec.trust: trusted`.

## Out of scope

- The `facts_kept` gate, retry, and fallback (042 A-2).
- Result events and records (043 A-3).
- MCP tools (051 H-13) and A2A (061 H-5).
- History swap (044 G-4, 045 A-4).

## Acceptance criteria

- [ ] `POST /v1/run` with a sample text returns simplified text from `simplifier-slm`, both streamed and complete.
- [ ] `POST /v1/chat/completions`, `POST /v1/messages`, and an `agents.task.requested.v1` event run the same core and return the same output for the same input and config version.
- [ ] On the S-4 held-out test set (≥ 300 records), the agent's output reaches `facts_kept` ≥ 0.95.
- [ ] The core imports no web framework or network code, and the testing kit's contract tests pass for every API, over A2A on localhost and in memory.
- [ ] Every response reports its config, prompt, and model route versions.
- [ ] Calls without the `simplify:run` scope are refused.

## Dependencies

- Depends on: [017 H-4](017-H-4-harness-features.md), [025 H-10](025-H-10-template-repo.md), [036 S-7](036-S-7-serve-with-vllm.md), [013 CH-2](013-CH-2-outbound-model-proxy.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md)
- Blocks: [042 A-2](042-A-2-evaluator-gate-fallback.md), [043 A-3](043-A-3-result-events.md), [047 M-3](047-M-3-shadow-mode.md)

## References

- Epic story: [A-1 in Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [Fig. 2](../slm-agent-platform-epic-v3.md#fig2) · [B.1](../slm-agent-platform-epic-v3.md#b1)
- Backlog plan: [000-plan.md](000-plan.md)
