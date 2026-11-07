---
title: "CH-15: Backpressure: a cap on runs in flight per replica, 503 overloaded, and a gauge to scale on"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 135
epic_id: CH-15
depends_on: ["017 H-4", "014 H-7"]
blocks: []
epic_refs: [G.4]
---

## Why

Nothing caps the runs in flight per replica. The HPA scales on CPU only, while the chassis idles on CPU waiting for the model. So overload shows up as 504s, not as a refusal a client can retry. The [product plan's gap review](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing) (2026-11-07) puts this first in its suggested phase G: runtime behavior under load and on rollout.

## What

- `spec.limits.max_in_flight` per replica (suggested: 32).
- Past the cap, the chassis answers 503 `overloaded` with `Retry-After` (suggested: 1 s), before the workload sees the call.
- A counter `chassis_requests_refused_total{reason="overloaded"}` and a gauge `chassis_runs_in_flight`.
- The `agent-lib` chart can scale the HPA on the gauge, as well as on CPU.
- The new code joins the contract's error list in its next version.

## Out of scope

- Agent pools on task queues and KEDA (104 O-3). They can read the same gauge.
- Throughput numbers under load (122 X-5, 133 X-5b).

## Acceptance criteria

- [ ] With `max_in_flight: 2` and two runs held open, a third call gets 503 `overloaded` with `Retry-After`, and the workload receives no call.
- [ ] A scrape test shows the refusal in `chassis_requests_refused_total{reason="overloaded"}` and the held runs in `chassis_runs_in_flight`.
- [ ] `max_in_flight` reloads with no restart, or `docs/guides/chassis-reference.md` says it is restart-only.
- [ ] A render test shows the HPA on the in-flight gauge when the chart's values ask for it.
- [ ] The contract lists `overloaded` with the version it was added in.

## Dependencies

- Depends on: [017 H-4](017-H-4-harness-features.md), [014 H-7](014-H-7-observability.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
