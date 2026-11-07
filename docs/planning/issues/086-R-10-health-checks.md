---
title: "R-10: Health checks: dead entries marked inactive, never deleted"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 86
epic_id: R-10
depends_on: ["076 R-1", "083 R-2", "014 H-7"]
blocks: []
epic_refs: [F.3]
---

## Why

In a registry with thousands of entries, some will stop working. Health checks keep dead entries out of results, so the orchestrator never picks a tool that is gone. Entries are marked `inactive` and never deleted, because the registry is also the AI system inventory and must keep its history.

## What

- A checker that polls every `active` entry on a set interval (suggested: 60 seconds, in config).
- Agents: `GET /health` and `GET /ready` on the chassis port (011 H-2). A dead workload behind a live chassis must fail `/ready`, so its entry is marked `inactive`. `/ready` checks that the workload answers through the connector: `ReadinessMonitor` (`packages/chassis/src/chassis/server/readiness.py`) calls `EngineConnector.probe()`. MCP servers: an MCP `ping`. REST APIs: a health URL set on the entry (suggested).
- After a set number of failures in a row (suggested: 3), the entry becomes `inactive`, with the reason and the time.
- One good check brings the entry back to `active`, but only if health checks made it inactive.
- Agents scaled to zero are not dead. Suggested: skip entries whose deployment exists with zero replicas, as seen by the controller (083 R-2).
- Refreshes live metrics per entry (`p95_latency_ms`, `error_rate`, cost) from Prometheus (014 H-7) on the same schedule. The queries reuse the dashboard expressions (`packages/chassis/src/chassis/dashboards/__init__.py`), with the dashboard's window (suggested: 5m):
  - `p95_latency_ms`: `1000 * histogram_quantile(0.95, sum by (le) (rate(chassis_run_duration_seconds_bucket{agent="<name>"}[5m])))`.
  - `error_rate`: `sum(rate(chassis_run_errors_total{agent="<name>"}[5m])) / sum(rate(chassis_run_duration_seconds_count{agent="<name>"}[5m]))` (suggested; the dashboard shows errors by code).
  - cost, in USD per hour: `sum(increase(chassis_model_cost_microusd_total{agent="<name>"}[1h])) / 1e6`.
- No delete path: the API has no delete call, and the registry's database role has no `DELETE` right on entries.
- Status changes publish `registry.entry.changed.v1`.

## Out of scope

- Search, which filters on the status set here (091 R-11).
- SLOs and alerts for agents (117 X-3).

## Acceptance criteria

- [ ] Stopping the echo agent marks its entry `inactive` after 3 failed checks, with reason and time; the row still exists.
- [ ] Starting it again brings the entry back to `active` after one good check.
- [x] A hung workload behind a live chassis fails `/ready` after 3 failed probes, and `/health` stays 200 · Delivered in PoC-4: `packages/chassis/tests/test_readiness.py::test_ready_goes_false_after_failed_probes_and_health_stays_ok` and `packages/chassis/tests/test_readiness.py::test_the_monitor_turns_unhealthy_after_n_failures_and_back_after_one_success`; `pocs/poc-04-stateless-scalable/tests/test_liveness.py::test_ready_goes_false_when_the_workload_hangs_and_health_stays_ok`; `pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md` (Compose pause drill: 503 after 9.7 s and 8.2 s, 200 again after 2.0 s and 1.4 s)
- [ ] With the workload container stopped and the chassis still running, the entry becomes `inactive` after 3 failed checks.
- [ ] An agent scaled to zero stays `active`.
- [ ] An entry sent to `pending` by review is not made `active` by a good health check.
- [ ] A delete through the API, or through the registry's database role, fails.
- [ ] Inactive entries are left out of default list results and returned with a status filter.
- [ ] The refreshed `p95_latency_ms`, `error_rate`, and cost on an entry equal the result of the named PromQL queries over the chassis series, within one interval.

## Dependencies

- Depends on: [076 R-1](076-R-1-registry-data-model-api.md), [083 R-2](083-R-2-kubernetes-controller.md), [014 H-7](014-H-7-observability.md)
- Blocks: none

## References

- Epic story: [R-10 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3)
- Backlog plan: [000-plan.md](000-plan.md)
