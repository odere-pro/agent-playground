---
title: "O-3: Agent pools on task queues with KEDA autoscaling"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 104
epic_id: O-3
depends_on: ["025 H-10", "100 O-2"]
blocks: ["105 O-4", "120 X-7", "122 X-5"]
epic_refs: [F.6, J]
---

## Why

Agents run in pools: groups of identical stateless containers behind Temporal task queues. Pools let the orchestrator spread work, and let each agent scale on load. Idle pools scale to zero, which is one of the main cost rules. This issue comes before fan-out, which needs parallel pools, and before the cost dashboard and load tests, which measure pools.

## What

- A task queue worker in the chassis: an inbound adapter that polls a Temporal task queue and calls the workload through the connector, like REST. Only the chassis holds the Temporal credential.
- The pool name comes from the agent config `scaling.queue` (for example `simplify`), and workflow steps pick it with `pool`.
- The orchestrator's agent port sends steps that name a `pool` to that task queue. Steps without one still use the native API.
- KEDA scales each pool on task queue backlog (suggested: the KEDA Temporal scaler), within `scaling.min_replicas` and `scaling.max_replicas`, set through the Helm chart.
- Event-driven agents scale on broker consumer lag with the KEDA scaler for the broker chosen in DEC-1.
- Both containers in a pod scale together. A `remote` workload pod scales on its own. A cold start includes both containers.
- Idle pools scale to zero. Evaluator pools run on CPU.
- Pods drain in-flight tasks on shutdown in the two-container order (gap (g) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups)): the workload finishes its in-flight `handle` calls, and both containers get a short preStop delay. suggested: the chassis stops polling the task queue at the start of its preStop delay.
- Pool metrics: queue length, replicas, and task latency.

## Reuse

- **Use:** the KEDA Temporal scaler (task-queue backlog, since KEDA 2.17), and the Kafka or NATS lag scalers.
- **Watch:** scale-to-zero can cut in-flight work (kedacore/keda#7368). Keep a minimum of one replica for busy pools, and rely on graceful shutdown. The chassis and the workload scale together, and a cold start includes both containers.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Fan-out logic (105 O-4).
- The cost dashboard (120 X-7) and load tests (122 X-5).
- GPU node setup (039 X-2).

## Acceptance criteria

- [ ] A workflow step with `pool: simplify` runs on a simplifier pod that pulled it from the `simplify` task queue.
- [ ] Under load, the pool scales up from 0 and never goes above `max_replicas`.
- [ ] With no work, the pool scales back to 0 after the cooldown.
- [ ] Killing a pool pod mid-task: the task is retried on another pod, and the run finishes.
- [ ] Scale-down never drops an in-flight task.
- [ ] An event-driven agent scales up when consumer lag grows on its topic.
- [ ] Queue length and replica count per pool show in Prometheus.

## Dependencies

- Depends on: [025 H-10](025-H-10-template-repo.md), [100 O-2](100-O-2-temporal-checkpoints.md)
- Blocks: [105 O-4](105-O-4-fan-out-fan-in.md), [120 X-7](120-X-7-cost-dashboard.md), [122 X-5](122-X-5-load-tests.md)

## References

- Epic story: [O-3 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [F.6](../slm-agent-platform-epic-v3.md#f6) · [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
