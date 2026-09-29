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
- Agents: `GET /health` and `GET /ready` on the chassis port (011 H-2). A dead workload behind a live chassis must fail `/ready`, so its entry is marked `inactive`. suggested: `/ready` checks that the workload answers through the connector. MCP servers: an MCP `ping`. REST APIs: a health URL set on the entry (suggested).
- After a set number of failures in a row (suggested: 3), the entry becomes `inactive`, with the reason and the time.
- One good check brings the entry back to `active`, but only if health checks made it inactive.
- Agents scaled to zero are not dead. Suggested: skip entries whose deployment exists with zero replicas, as seen by the controller (083 R-2).
- Refreshes live metrics per entry (`p95_latency_ms`, `error_rate`, cost) from Prometheus (014 H-7) on the same schedule.
- No delete path: the API has no delete call, and the registry's database role has no `DELETE` right on entries.
- Status changes publish `registry.entry.changed.v1`.

## Out of scope

- Search, which filters on the status set here (091 R-11).
- SLOs and alerts for agents (117 X-3).

## Acceptance criteria

- [ ] Stopping the echo agent marks its entry `inactive` after 3 failed checks, with reason and time; the row still exists.
- [ ] Starting it again brings the entry back to `active` after one good check.
- [ ] With the workload container stopped and the chassis still running, `/ready` fails, and the entry becomes `inactive` after 3 failed checks.
- [ ] An agent scaled to zero stays `active`.
- [ ] An entry sent to `pending` by review is not made `active` by a good health check.
- [ ] A delete through the API, or through the registry's database role, fails.
- [ ] Inactive entries are left out of default list results and returned with a status filter.
- [ ] Live metrics on an entry match Prometheus within one check interval.

## Dependencies

- Depends on: [076 R-1](076-R-1-registry-data-model-api.md), [083 R-2](083-R-2-kubernetes-controller.md), [014 H-7](014-H-7-observability.md)
- Blocks: none

## References

- Epic story: [R-10 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3)
- Backlog plan: [000-plan.md](000-plan.md)
