---
title: "X-7: Cost dashboard per agent and per pool"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:S", "layer:platform"]
milestone: "W10 Production readiness"
index: 120
epic_id: X-7
depends_on: ["004 G-2", "104 O-3"]
blocks: []
epic_refs: [J]
---

## Why

The epic's goal is lower cost, so cost must be visible where it is spent: per agent and per pool. The router already counts API tokens and cost. This issue adds GPU and CPU hosting cost from the pools, so cost per 1,000 requests can be compared with the baseline. It comes after agent pools, which it measures.

## What

- A Grafana dashboard with cost per agent: API tokens and cost from the router, plus the hosting cost of the agent's pool.
- Cost per pool: replica-hours by node type (GPU, CPU, spot) times the rates from the cost model (031 G-5).
- Cost per 1,000 requests per agent, next to the baseline from the baseline report (005 G-3).
- Scale to zero per pool: replicas and idle time over time.
- Share of requests sent to the big model per agent.
- The chassis sidecar's reserved CPU and memory per replica, as its own cost line per agent and pool. ADR-001 says the sidecar's reserved CPU times the number of replicas drives its bill.
- Rates kept as config, so a price change needs no dashboard change.

## Out of scope

- The savings dashboard for token spend (006 G-6).
- Billing per tenant, a non-goal of the epic.
- Cost alerts, which can build on the SLO alerting later (117 X-3).

## Acceptance criteria

- [ ] The dashboard shows cost per agent and per pool for the last day, week, and month.
- [ ] API cost totals match the router's counts for the same period.
- [ ] Hosting cost per pool matches replica-hours times the configured rates.
- [ ] Cost per 1,000 requests for the simplifier shows next to the baseline.
- [ ] A pool that scaled to zero shows zero hosting cost for that time.
- [ ] The chassis sidecar's cost shows as its own line, and matches its reserved CPU and memory times replica-hours at the configured rates.

## Dependencies

- Depends on: [004 G-2](004-G-2-token-cost-counting.md), [104 O-3](104-O-3-agent-pools-keda.md)
- Blocks: none

## References

- Epic story: [X-7 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
