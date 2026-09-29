---
title: "G-6: Savings dashboard with live token spend"
labels: ["story", "priority:P0", "phase:1-router", "area:router", "size:M", "layer:platform"]
milestone: "W1 Router and baseline"
index: 6
epic_id: G-6
depends_on: ["004 G-2"]
blocks: ["096 P-2a"]
epic_refs: [G.2, success-metrics]
---

## Why

The epic goal is to cut token use and cost, and this dashboard is where that shows. It is P0 because it is how the epic goal is made visible, from the first week of router traffic. It covers the "dashboard shows live token spend" part of the Phase 1 done-when, and the `metrics_get` MCP tool (096 P-2a) later reads the same data.

## What

- A Grafana dashboard fed by the Prometheus metrics from 004 G-2.
- Panels for live token spend and cost, per agent, per user, and per route.
- Requests and cost per 1,000 requests over time.
- Big-model routes and SLM routes shown apart, as tokens and as share of requests, so savings show once the SLM ships.
- A baseline line and a −30% target line, both set from one config value that is filled in from the 005 G-3 report.
- The dashboard stored as code (suggested: Grafana provisioning files), so it loads the same way locally and in the cloud.
- Prometheus and Grafana in Docker Compose next to the router.

## Out of scope

- The simplifier agent dashboard (046 A-5).
- The `metrics_get` MCP tool (096 P-2a).
- The cost dashboard per agent and pool, with GPU cost (120 X-7).

## Acceptance criteria

- [ ] The dashboard shows token spend and cost from live router traffic.
- [ ] A test call through the router shows on the dashboard within one minute.
- [ ] Spend can be filtered by agent, user, and route.
- [ ] Big-model and SLM routes are shown apart.
- [ ] The baseline and target lines come from one config value, set from 005 G-3.
- [ ] `docker compose up` loads the dashboard with no manual steps.

## Dependencies

- Depends on: [004 G-2](004-G-2-token-cost-counting.md)
- Blocks: [096 P-2a](096-P-2a-mcp-golden-set-metrics-tools.md)

## References

- Epic story: [G-6 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
