---
title: "X-5b: Load tests for agent pools, the registry, and event bursts"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 133
epic_id: X-5b
depends_on: ["091 R-11", "104 O-3", "117 X-3"]
blocks: []
epic_refs: [G.4]
---

## Why

122 X-5 was split on 2026-11-06. Its phase A half, the throughput rerun and the sidecar overhead on a Linux host, stays in 122 X-5. The load tests here need agent pools (104 O-3) and the registry search (091 R-11), which are `later`, and they pass or fail against the SLOs of 117 X-3. So they have their own issue and a different owner.

## What

- The load tests that need agent pools, the registry, and the broker: scale to zero and back, a pool's p95 at the target load, registry search at 5,000 entries, and an event burst.
- Split from 122 X-5 on 2026-11-06. Pass or fail is against the 117 X-3 SLOs.
- The pool under test is the echo agent's pool: the simplifier is parked with the SLM track.

## Out of scope

- The Linux rerun and the sidecar overhead (122 X-5).
- Fixes for the bottlenecks found; each gets its own issue.

## Acceptance criteria

- [ ] The pool test runs on staging and shows scale-up from 0 and back to 0.
- [ ] The echo agent's pool meets its p95 latency SLO at the target load (suggested: twice the current peak).
- [ ] Registry search meets its p95 latency SLO with at least 5,000 entries.
- [ ] Registry search keeps the correct main tool in the top 3 for ≥ 90% of the search test set under load.
- [ ] The event burst drains with no lost events and nothing in the dead-letter topic.
- [ ] Each run produces a report, and each bottleneck found is filed as an issue.

## Dependencies

- Depends on: [091 R-11](091-R-11-hybrid-search.md), [104 O-3](104-O-3-agent-pools-keda.md), [117 X-3](117-X-3-slos-alerts.md)
- Blocks: none

## References

- Epic story: [X-5b in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
