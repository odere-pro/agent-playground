---
title: "G-2: Token and cost counting per request, user, and agent"
labels: ["story", "priority:P0", "phase:1-router", "area:router", "size:M", "layer:platform"]
milestone: "W1 Router and baseline"
index: 4
epic_id: G-2
depends_on: ["002 G-1"]
blocks: ["005 G-3", "006 G-6", "012 H-3", "120 X-7"]
epic_refs: [J]
---

## Why

Token and cost numbers are the core measurement of the epic. The baseline (005 G-3), the savings dashboard (006 G-6), and later the cost dashboard (120 X-7) all read them. Counting must start as soon as the router is up, so the two-week baseline clock can start on the same day.

## What

- Input tokens, output tokens, and cost counted for every request through the router, using LiteLLM token and cost counting.
- Each request tagged with user, agent, and route, so spend can be grouped per request, per user, and per agent.
- The agent is taken from the service's virtual key (002 G-1), so a caller cannot spoof it. The tag sits in the key's `metadata.tags` (`deploy/kind/poc05/platform/seed.sh`). The proving test is a kind test, suggested: `test_spoofed_agent_tag_counts_under_key_agent` (H31 in `pocs/poc-05-sandboxed/notes/2026-10-02-blind-spots.md`, never run). suggested: `user` still comes from request metadata; see ADR-008 open questions.
- Calls with no `user` tag counted under `unknown`, never dropped.
- One usage record per request stored in Postgres, with `request_id` and `trace_id` when the caller sends them.
- A price table per route in router config.
- Streaming calls counted the same way as complete calls. A correlated stream the client abandons is charged on the Anthropic proxy route (`packages/chassis/tests/test_model_proxy_messages_budget.py::test_an_abandoned_stream_is_charged_its_reservation_when_usage_is_unknown`). On the chat route it is charged nothing yet, a known gap of this issue (`packages/chassis/tests/test_model_proxy.py::test_chat_proxy_charges_nothing_when_a_correlated_stream_is_abandoned`).
- Token and cost counters exposed as Prometheus metrics, labeled by agent and route. `user` is in the usage record only, because a `user` label is unbounded.

## Out of scope

- The baseline report (005 G-3).
- The savings dashboard (006 G-6) and the cost dashboard per agent and pool (120 X-7).
- GPU hosting cost and the break-even point (031 G-5).
- SLM route prices (a self-hosted SLM route has a token price of zero) and the day collection starts, for the baseline window. Parked with 005 G-3.

## Acceptance criteria

- [ ] Every call through the router makes one usage record with input tokens, output tokens, cost, route, user, agent, and time.
- [ ] Token counts match the usage the provider reports, on a sample of calls to each big model.
- [x] A streamed call makes a usage record with real token counts, not zero. · Delivered in PoC-1: `pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md` (streamed runs on both routes, each with a `metrics` event of 42 input and 9 output tokens); `packages/chassis/tests/test_metrics.py::test_model_tokens_and_cost_come_from_the_proxy_usage` (fake model)
- [ ] One query returns totals per request, per user, and per agent for a date range.
- [ ] Calls with no `user` tag show under `unknown`.
- [ ] A call that sends another agent's name in its metadata is still counted under the agent of its key.
- [ ] Prometheus scrapes the token and cost metrics, labeled by agent and route; `user` is in the usage record only.
  - Open. Only the chassis `GET /metrics` side is proven (fake model): `packages/chassis/tests/test_metrics.py::test_token_and_cost_series_are_labeled_by_agent_and_route_never_by_user` shows no `user` label or value in the scrape. The LiteLLM side, and the positive half (`user` is in the usage record), stay open and need a real router.

## Dependencies

- Depends on: [002 G-1](002-G-1-litellm-router.md)
- Blocks: [005 G-3](005-G-3-baseline-report.md), [006 G-6](006-G-6-savings-dashboard.md), [012 H-3](012-H-3-model-port.md), [120 X-7](120-X-7-cost-dashboard.md)

## References

- Epic story: [G-2 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
