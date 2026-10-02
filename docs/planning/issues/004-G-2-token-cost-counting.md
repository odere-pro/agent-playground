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
- The agent is taken from the service's virtual key (002 G-1), so a caller cannot spoof it. suggested: `user` still comes from request metadata.
- Calls with no `user` tag counted under `unknown`, never dropped.
- One usage record per request stored in Postgres, with `request_id` and `trace_id` when the caller sends them.
- A price table per route in router config. Self-hosted SLM routes have a token price of zero here; GPU hosting cost is added by the cost model (031 G-5).
- Streaming calls counted the same way as complete calls.
- Token and cost counters exposed as Prometheus metrics, labeled by agent, user, and route.
- Status after PoC-1: token counts per call are visible in the router through a custom callback (`deploy/compose/litellm/token_log.py`). It prints one line per call with route, tokens, cost, and `tags=agent:<name>`, because there is no Postgres and `/spend/logs` answers `No connected db.` Streamed calls log real counts, not zero. The agent tag comes from the request metadata, so it is spoofable until a virtual key carries it. The criterion "counted under the agent of its key" stays open and needs the virtual-key work. Cost is `0.000000` for the fake model; a real cost figure is unverified until the `local` override runs. See `pocs/poc-01-walking-skeleton/notes/2026-09-29-compose-key-debt.md`.

## Out of scope

- The baseline report (005 G-3).
- The savings dashboard (006 G-6) and the cost dashboard per agent and pool (120 X-7).
- GPU hosting cost and the break-even point (031 G-5).

## Acceptance criteria

- [ ] Every call through the router makes one usage record with input tokens, output tokens, cost, route, user, agent, and time.
- [ ] Token counts match the usage the provider reports, on a sample of calls to each big model.
- [ ] A streamed call makes a usage record with real token counts, not zero.
- [ ] One query returns totals per request, per user, and per agent for a date range.
- [ ] Calls with no `user` tag show under `unknown`.
- [ ] A call that sends another agent's name in its metadata is still counted under the agent of its key.
- [ ] Prometheus scrapes the token and cost metrics, labeled by agent, user, and route.
- [ ] The day collection starts is recorded, so the baseline window can be counted from it.

## Dependencies

- Depends on: [002 G-1](002-G-1-litellm-router.md)
- Blocks: [005 G-3](005-G-3-baseline-report.md), [006 G-6](006-G-6-savings-dashboard.md), [012 H-3](012-H-3-model-port.md), [120 X-7](120-X-7-cost-dashboard.md)

## References

- Epic story: [G-2 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
