---
title: "X-5: Load tests for agent pools and the registry"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 122
epic_id: X-5
depends_on: ["091 R-11", "104 O-3"]
blocks: []
epic_refs: [G.4]
---

## Why

The registry is designed for thousands of entries, and pools must scale under real load. Load tests show whether they do before users find out. They also measure the chassis sidecar, which [ADR-001](../adr/001-chassis-delivery-model.md) sized from estimates only. They come after agent pools and hybrid search, the two parts they test.

## What

- Load test scripts in the repo (suggested: Locust, in Python like the rest of the stack).
- Pool test: ramp load on the `simplify` pool through the orchestrator and through the native API. Watch KEDA scale up, latency, errors, and scale to zero after.
- Registry test: search load against a registry seeded with at least 5,000 entries (suggested size), with SQL filters, keyword search, vector search, and the reranker all on.
- Event test: a burst of `agents.task.requested.v1` events, watching consumer lag and KEDA.
- Sidecar test: during the pool test, measure the chassis sidecar's CPU and memory at p95, and the extra latency of the local hop at p50 and p95. Compare them with the ADR's estimates: 0.05–0.1 vCPU, 128–256 MiB, and 1–3 ms.
- The library chart's resource requests (024 CH-3) set from the measured p95.
- A check of the ADR's Revisit trigger. If the measured numbers are far above the estimates, the owner decides whether to allow `inprocess` in production for small, trusted Python services. suggested: "far above" means more than twice the upper estimate.
- Pass or fail against the X-3 SLOs.
- A report per run: requests per second, p95 latency, error rate, replicas over time, and the first bottleneck.
- Status after PoC-4, a first measurement before this issue's pool test (Docker Desktop, one host, the fake model server; [load results](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-load-results.md)). Next to ADR-001's suggested figures: memory 150 to 182 MiB, inside 128–256 MiB; CPU 0.08 to 0.14 vCPU idle and 0.105 to 0.129 vCPU at 10 RPS, at or above the top of 0.05–0.1 vCPU; about 0.6 to 1.3 vCPU per 100 RPS at saturation; the local hop is below the method's 1 ms resolution and was not isolated, so it is not yet compared with 1–3 ms. The CPU is not more than twice the upper estimate, so the suggested "far above" rule is not met; at or above the top of the range, it goes to the epic owner as an input to ADR-001's Revisit rule. Throughput grew cleanly with pairs for `echo-pydanticai` only (4 strict xfails on other steps), and 4 pairs do not fit the Docker Desktop VM. This issue still sets the chart's requests from a cluster p95 and isolates the hop. PoC-4 closed on 2026-10-08 with its throughput criterion flagged. This issue owns the rerun on a Linux host or with pinned CPUs, for every engine.

## Out of scope

- Fixes for the bottlenecks found; each gets its own issue.
- Cost per pool under load (120 X-7).
- New SLO rules (117 X-3).
- Changing ADR-001 if the Revisit trigger is met. That needs a new ADR.

## Acceptance criteria

- [ ] The pool test runs on staging and shows scale-up from 0 and back to 0.
- [ ] The simplifier pool meets its p95 latency SLO at the target load (suggested: twice the current peak).
- [ ] Registry search meets its p95 latency SLO with at least 5,000 entries.
- [ ] Registry search keeps the correct main tool in the top 3 for ≥ 90% of the search test set under load.
- [ ] The event burst drains with no lost events and nothing in the dead-letter topic.
- [ ] Each run produces a report, and each bottleneck found is filed as an issue.
- [ ] The report lists the chassis sidecar's p95 CPU and memory, and the local hop's extra latency at p50 and p95, next to the ADR's estimates (0.05–0.1 vCPU, 128–256 MiB, 1–3 ms).
- [ ] The library chart's resource requests are set from the measured p95, and the report says whether the ADR's Revisit trigger is met.

## Dependencies

- Depends on: [091 R-11](091-R-11-hybrid-search.md), [104 O-3](104-O-3-agent-pools-keda.md)
- Blocks: none

## References

- Epic story: [X-5 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
