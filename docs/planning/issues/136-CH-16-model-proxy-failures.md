---
title: "CH-16: The model proxy under failure: budget refusal code, per-call timeout, LiteLLM retries and fallbacks"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 136
epic_id: CH-16
depends_on: ["013 CH-2", "017 H-4", "003 G-1b", "117 X-3"]
blocks: []
epic_refs: [F.1, G.1]
---

## Why

Three model-proxy paths fail quietly today. LiteLLM's daily budget refusal reaches the workload as `http_400`, with no counter and no alert. The proxy's upstream call has no per-call timeout, so a cancelled run keeps paying for it (`packages/chassis/src/chassis/adapters/litellm/client.py`). And the retry stage (017 H-4) is planned as a rerun of the whole agent, while LiteLLM has no `num_retries` or `fallbacks` per route. The [product plan's gap review](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing) (2026-11-07) puts all three in its suggested phase G.

## What

- A fixed code `service_budget_exhausted` on both proxy routes, OpenAI and Anthropic, when LiteLLM refuses for the service's budget. A counter `chassis_model_budget_exhausted_total{route}`, and an alert on it with the 117 X-3 rules.
- The run's remaining time goes into `ModelPort.complete` and `.stream`. The upstream call stops when the run ends.
- `num_retries` and `fallbacks` set per route in the LiteLLM config. The chassis retry stage then covers only the errors LiteLLM cannot see.

## Out of scope

- The run budget's own refusal, `budget_exhausted` as `http_429`: P-11 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md).
- The gate's retry on a low score (017 H-4, 042 A-2).

## Acceptance criteria

- [ ] With the fake model server answering LiteLLM's budget refusal, the workload gets `service_budget_exhausted` on the OpenAI and the Anthropic route, and the counter rises per route.
- [ ] An alert rule on `chassis_model_budget_exhausted_total` sits with the 117 X-3 rules and fires in a fault test.
- [ ] A run cancelled or timed out during a slow fake model call ends the upstream call, and the fake sees the connection close.
- [ ] Each route in the LiteLLM config sets `num_retries` and `fallbacks`, and a stack test shows a provider error answered by the fallback with no chassis retry.
- [ ] The contract lists `service_budget_exhausted` with the version it was added in.

## Dependencies

- Depends on: [013 CH-2](013-CH-2-outbound-model-proxy.md), [017 H-4](017-H-4-harness-features.md), [003 G-1b](003-G-1b-named-model-routes.md), [117 X-3](117-X-3-slos-alerts.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [G.1](../slm-agent-platform-epic-v3.md#g1)
- Backlog plan: [000-plan.md](000-plan.md)
