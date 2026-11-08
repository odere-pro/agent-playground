---
title: "O-3: Agent pools on task queues with KEDA autoscaling"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:L", "layer:platform"]
milestone: "W9 Orchestrator"
index: 104
epic_id: O-3
depends_on: ["025 H-10", "100 O-2"]
blocks: ["105 O-4", "133 X-5b"]
epic_refs: [F.6, J]
---

## Why

Agents run in pools: groups of identical stateless containers behind Temporal task queues. Pools let the orchestrator spread work, and let each agent scale on load. Idle pools scale to zero, which is one of the main cost rules. This issue comes before fan-out, which needs parallel pools, and before the cost dashboard and load tests, which measure pools.

## What

- A task queue worker in the chassis: an inbound adapter that polls a Temporal task queue and calls the workload through the connector, like REST. Only the chassis holds the Temporal credential.
- The pool name comes from the agent config `scaling.queue` (for example `echo`), and workflow steps pick it with `pool`.
- The orchestrator's agent port sends steps that name a `pool` to that task queue. Steps without one still use the native API.
- KEDA scales each pool on task queue backlog (suggested: the KEDA Temporal scaler), within `scaling.min_replicas` and `scaling.max_replicas`, set through the library chart values in `deploy/helm/agent-lib`.
- Event-driven agents scale on broker consumer lag with the KEDA scaler for the broker chosen in DEC-1.
- Both containers in a pod scale together. A cold start includes both containers.
- Idle pools scale to zero. Evaluator pools run on CPU.
- Pods drain in-flight tasks on shutdown in the order the chart uses since [ADR-012](../adr/012-drain-order-on-rollout.md): the chassis is the main container and the workload the native sidecar (`deploy/helm/agent-lib/templates/_deployment.tpl`), so the chassis stops first, `/ready` goes false first, and in-flight runs finish (gap (g) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups)). suggested: the chassis stops polling the task queue when `/ready` goes false.
- Pool metrics: queue length, replicas, and task latency.
- Per-pool hosting cost, scale-to-zero, and replica-hours on the cost dashboard, with rates from config. This moved here from 120 X-7 on 2026-11-06.
- Status after PoC-4 and PoC-9: facts, not ticks. PoC-4 proved request-level drain, kill, and rolling restart on Compose and kind ([demo](../../../pocs/poc-04-stateless-scalable/demo/2026-10-01-demo-scale.md), [container roles](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-container-roles.md); `pocs/poc-04-stateless-scalable/tests/test_killed_replica.py::test_a_retry_after_a_dead_owner_runs_once_after_the_lease`, `test_graceful_shutdown.py::test_the_chassis_drains_in_flight_runs_and_ready_goes_false_first`). The PoC-9 HPA drill scaled 2 to 4 replicas at 203% CPU, and scale-down was not waited for ([HPA drill](../../../pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md)). No task queue, KEDA, scale from 0, or lag scaling exists. The product plan runs CPU HPA only; KEDA pools are `later`.

## Reuse

- **Use:** the KEDA Temporal scaler (task-queue backlog, since KEDA 2.17), and the Kafka or NATS lag scalers.
- **Watch:** scale-to-zero can cut in-flight work (kedacore/keda#7368). Keep a minimum of one replica for busy pools, and rely on graceful shutdown. The chassis and the workload scale together, and a cold start includes both containers.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Fan-out logic (105 O-4).
- The cost dashboard per agent (120 X-7) and the load tests (122 X-5). The pool load tests are 133 X-5b.
- GPU node setup (039 X-2).
- A `remote` workload pod that scales on its own. The product plan runs a `remote` pod as one replica with no HPA; this is `later`.

## Acceptance criteria

- [ ] A workflow step with `pool: echo` runs on an echo agent pod that pulled it from the `echo` task queue.
- [ ] Under load, the pool scales up from 0 and never goes above `max_replicas`.
- [ ] With no work, the pool scales back to 0 after the cooldown.
- [ ] Killing a pool pod mid-task: the task is retried on another pod, and the run finishes.
- [ ] Scale-down never drops an in-flight task.
- [ ] An event-driven agent scales up when consumer lag grows on its topic.
- [ ] Queue length and replica count per pool show in Prometheus.
- [ ] Per-pool hosting cost, scale-to-zero, and replica-hours show on the cost dashboard, with rates from config.

## Dependencies

- Depends on: [025 H-10](025-H-10-template-repo.md), [100 O-2](100-O-2-temporal-checkpoints.md)
- Blocks: [105 O-4](105-O-4-fan-out-fan-in.md), [133 X-5b](133-X-5b-load-tests-pools-registry.md)

## References

- Epic story: [O-3 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [F.6](../slm-agent-platform-epic-v3.md#f6) · [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
