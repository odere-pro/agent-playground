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
- The dashboard stored as code, so it loads the same way locally and in the cloud. PoC-8 does this: `python -m chassis.dashboards` writes `deploy/dashboards/` (`packages/chassis/tests/test_dashboards.py::test_the_generated_files_are_up_to_date`).
- Prometheus and Grafana next to the router, in the PoC-8 stack (`deploy/compose/poc08/stack.sh`). An offline check holds the provisioning (`pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_static.py::test_grafana_provisions_every_file_of_deploy_dashboards`). The live checks (`pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py`, `test_grafana_loaded_every_dashboard_file` and `test_every_panel_query_has_data`) are `network` tests with no dated record yet. One dated stack record closes the first box and the last.

## Out of scope

- The container dashboard: the north star, its inputs, and the counters (046 A-5). This issue is its token and cost input.
- The `metrics_get` MCP tool (096 P-2a).
- The cost dashboard per agent and pool, with GPU cost (120 X-7).
- Big-model and SLM routes shown apart, and the baseline and −30% target lines set from 005 G-3. Parked with the SLM track.

## Acceptance criteria

- [ ] The dashboard shows token spend and cost from live router traffic.
- [ ] A test call through the router shows on the dashboard within one minute.
- [ ] Spend can be filtered by agent, user, and route.
- [ ] The PoC-8 stack loads every dashboard with no manual steps.

## Dependencies

- Depends on: [004 G-2](004-G-2-token-cost-counting.md)
- Blocks: [096 P-2a](096-P-2a-mcp-golden-set-metrics-tools.md)

## References

- Epic story: [G-6 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
