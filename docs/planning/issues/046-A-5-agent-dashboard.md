---
title: "A-5: Container dashboard and per-agent gate panels"
labels: ["story", "priority:P1", "phase:3-simplifier-agent", "area:agent", "size:M", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 46
epic_id: A-5
depends_on: ["001 DEC-1", "014 H-7", "042 A-2"]
blocks: ["048 M-4", "075 E-3", "096 P-2a"]
epic_refs: [G.2, success-metrics]
---

## Why

Phase E needs one container dashboard: the north star, its inputs, and the counter metrics, next to the gate panels per agent. It follows the gate (042 A-2), which produces the run, retry, and fallback counts. The generator is `python -m chassis.dashboards`, which writes `deploy/dashboards/`.

## What

- A Grafana dashboard built from code on the Prometheus `/metrics` (014 H-7), with the token and cost input from 006 G-6.
- Three rows from `docs/specs/metrics.md`: the north star, its inputs, and the counter metrics.
- Gate panels per agent: first-try pass rate; retry rate; fallback rate with a target line at 10%. 1 − (retries + fallbacks) / runs is not a first-try rate: a run that retries and then falls back adds to both counters, and a run with two retries adds 2.
- Links from panels to Langfuse traces and Loki logs.
- A filter by config version and model route version needs a label the metric families do not carry ([contract v7 A.3](../../contracts/contract-v7.md#a3-the-families)). It waits on a contract change.

## Out of scope

- SLOs and alerts (117 X-3).
- Cost per agent and per pool (120 X-7).
- The router savings dashboard (006 G-6).
- Tokens saved and the G-3 baseline: parked with the simplifier.
- A dashboard in the generated repo: `agentctl` ships no dashboards (059 H-15).
- A scripted 100-call load with 10 forced fallbacks: replaced by the PoC-8 stack drill.

## Acceptance criteria

- [ ] The dashboard shows every panel with data, in Docker Compose and on Kubernetes. Open: `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py` (`test_every_panel_query_has_data`, `test_grafana_has_the_agent_dashboard_with_every_panel`) is `network`-marked and waits on run 41011346864; no Kubernetes path exists.
- [ ] The north star, its inputs, and the counter metrics from `docs/specs/metrics.md` each have a row.
- [x] First-try pass rate and retry rate show per agent, from the gate counters. · Delivered by: Offline, `packages/chassis/tests/test_dashboards.py::test_the_gate_panels_work_out_from_fixed_counter_values`, `packages/chassis/tests/test_evaluator_gate_first_attempt.py::test_the_first_try_rate_counts_only_scored_first_attempts`; the run-level counter suggested: `chassis_gate_first_attempts_total{mode, outcome}` is an additive contract change (decided 2026-11-13), and the panel is first attempts that passed over first attempts scored, one line per mode (sync and async). Data on a live stack is the first box
- [x] The fallback rate shows per agent, with a target line at 10%. · Delivered by: Offline, `packages/chassis/tests/test_dashboards.py::test_the_fallback_panel_draws_a_target_line_at_ten_percent`
- [x] Panels link to Langfuse traces and Loki logs. · Delivered by: Offline, `packages/chassis/tests/test_dashboards.py::test_every_panel_links_to_langfuse_traces_and_loki_logs`; the Langfuse link is `${langfuse_url}/project/${langfuse_project}/traces`, and `langfuse_url`, `langfuse_project`, `loki_uid`, and the Loki label `agent` are suggested: per deploy (nothing in `deploy/` provisions that label yet)
- [ ] Filtering by config version and model route version splits the numbers, once the metric families carry that label (a contract change).
- [x] The dashboard is provisioned from code, not built by hand, and a test checks that the file is up to date. · Delivered in PoC-8: `packages/chassis/tests/test_dashboards.py::test_the_generated_files_are_up_to_date`, `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_static.py::test_grafana_provisions_every_file_of_deploy_dashboards`

## Dependencies

- Depends on: [001 DEC-1](001-DEC-1-resolve-open-decisions.md), [014 H-7](014-H-7-observability.md), [042 A-2](042-A-2-evaluator-gate-fallback.md)
- Blocks: [048 M-4](048-M-4-canary-rollback.md), [075 E-3](075-E-3-evaluator-replaces-judge.md), [096 P-2a](096-P-2a-mcp-golden-set-metrics-tools.md)

## References

- Epic story: [A-5 in Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
