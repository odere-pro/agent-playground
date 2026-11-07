---
title: "X-7: Cost dashboard per agent and per pool"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:S", "layer:platform"]
milestone: "W10 Production readiness"
index: 120
epic_id: X-7
depends_on: ["004 G-2"]
blocks: []
epic_refs: [J]
---

## Why

The epic's goal is lower cost, so cost must be visible where it is spent: per agent and per good task. The chassis already counts API tokens and cost from the model proxy. This issue puts that cost, over a day, a week, and a month, on the container dashboard (046 A-5). Per-pool hosting cost moved to 104 O-3 on 2026-11-06, because pools are `later`.

## What

- Cost per agent and per good task, for a day, a week, and a month, on the container dashboard (046 A-5). API tokens and cost come from the model proxy's counts (`chassis_model_cost_microusd_total`; `packages/chassis/tests/test_metrics.py::test_model_tokens_and_cost_come_from_the_proxy_usage`).
- Cost per 1,000 requests per agent, next to the first week's number. The baseline report (005 G-3) is parked, so the first week stands in for it.
- Share of requests sent to the big model per agent.
- Rates kept as config, not taken from the cost model (031 G-5, parked), so a price change needs no dashboard change.
- Today a per-agent "Model cost" panel shows cost per hour. It has no day, week, or month window, so box 1 is open until the windows are added.

## Out of scope

- The savings dashboard for token spend (006 G-6).
- Billing per tenant, a non-goal of the epic.
- Cost alerts, which can build on the SLO alerting later (117 X-3).
- Per-pool hosting cost, scale-to-zero, and replica-hours (104 O-3).
- The chassis sidecar's cost line, which is in the load test report (122 X-5).

## Acceptance criteria

- [ ] The container dashboard shows cost per agent and per good task for the last day, week, and month.
- [ ] API cost totals match the model proxy's counts for the same period.
- [ ] Cost per 1,000 requests per agent shows next to the first week's number.

## Dependencies

- Depends on: [004 G-2](004-G-2-token-cost-counting.md)
- Blocks: none

## References

- Epic story: [X-7 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
