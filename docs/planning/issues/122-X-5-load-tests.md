---
title: "X-5: Load tests: throughput and sidecar overhead on a Linux host"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 122
epic_id: X-5
depends_on: []
blocks: []
epic_refs: [G.4]
---

## Why

PoC-4 closed on 2026-10-08 with its throughput criterion flagged: on Docker Desktop, throughput grew cleanly with pairs for one engine only, and 4 pairs did not fit the VM. The product plan's phase A asks for every PoC-1 to PoC-5 flag to have plain evidence. This issue reruns the throughput test on a Linux host with pinned CPUs, for every engine, and measures the chassis sidecar, which [ADR-001](../adr/001-chassis-delivery-model.md) sized from estimates only. The pool, registry, and event-burst load tests moved to 133 X-5b, because they need parts that are `later`.

## What

- The tools exist: `make load-test` and `python -m load_tools.run_matrix --engine X` (`packages/load-tools`).
- The host is an owner-list item (011 section 3). It needs enough cores for 8 chassis and workload containers at 1 vCPU each, plus Traefik, Valkey, MinIO, and the model server.
- Throughput: the rerun covers every engine at 1, 2, and 4 pairs. The four strict xfails in `pocs/poc-04-stateless-scalable/tests/test_load_results.py` (`RECORDED_EXCEPTIONS`: `echo-python-1to2`, `echo-python-2to4`, `echo-langgraph-2to4`, `echo-typescript-2to4`) must pass.
- Sidecar test: the chassis sidecar's CPU and memory at p95, and the extra latency of the local hop at p50 and p95, timed by a chassis-side timer. Compare them with the ADR's estimates: 0.05–0.1 vCPU, 128–256 MiB, and 1–3 ms.
- The "before" numbers, from PoC-4 (Docker Desktop, one host, the fake model server; [load results](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-load-results.md)): CPU 0.105 to 0.129 vCPU at 10 RPS, at or above the top of 0.05–0.1; memory 150 to 182 MiB, inside 128–256. The note gives CPU and memory as a mean and a max, not a p95. The hop is below the method's 1 ms resolution and was not isolated. `test_the_sidecar_hop_is_recorded` checks that the numbers are recorded, not that they are p95 or isolated. The sidecar box needs a proving test (suggested: `test_the_sidecar_p95_cpu_and_memory_are_recorded`).
- The sidecar's cost at the configured rates goes in the report. It moved here from 120 X-7.
- The library chart's resource requests (024 CH-3, `deploy/helm/agent-lib`) set from the measured p95.
- A check of the ADR's Revisit trigger. If the measured numbers are far above the estimates, the owner decides whether to allow `inprocess` in production for small, trusted Python services. suggested: "far above" means more than twice the upper estimate. The PoC-4 note's reading: the CPU is at or above the top of the range, not above twice the upper estimate, so the rule is not met. The rule stays `suggested:` until the owner accepts it.
- The rare 502 under load: about 1 in 4,600 calls at 4 pairs, from the chassis or Traefik, not yet known (011 section 4).
- A report per run: requests per second, p95 latency, error rate, replicas over time, and the first bottleneck.

## Out of scope

- Fixes for the bottlenecks found; each gets its own issue.
- Pool scale to zero, the pool p95, registry search p95 at 5,000 entries, the registry top 3, and the event burst: moved to 133 X-5b.
- Cost per pool (104 O-3).
- New SLO rules (117 X-3).
- Changing ADR-001 if the Revisit trigger is met. That needs a new ADR.
- `make load-smoke` in a rendered agent (`packages/agent-testkit/src/agent_testkit/suites.py::LoadSmoke.test_concurrent_runs_all_complete`, 24 concurrent calls through the chassis). It is a smoke test, not a substitute for this issue's runs.

## Acceptance criteria

- [ ] Throughput grows with replicas for every engine on a Linux host with pinned CPUs, numbers recorded; the four strict xfails in `pocs/poc-04-stateless-scalable/tests/test_load_results.py::test_throughput_grows_with_pairs` (`RECORDED_EXCEPTIONS`) pass and the list is emptied; PoC-4 criterion 4 is ticked in its README.
- [ ] The report gives the sidecar's p95 CPU and memory and the local hop at p50 and p95, isolated by a chassis-side timer, next to ADR-001's 0.05 to 0.1 vCPU, 128 to 256 MiB, and 1 to 3 ms, and the sidecar's cost at the configured rates.
- [ ] The library chart's requests are set from that p95, and the report says whether the ADR-001 Revisit trigger is met (suggested: more than twice the upper estimate).
- [ ] Each run produces a report, and each bottleneck found is filed as an issue.
- [ ] The 502 rate at 4 pairs is recorded and the source (chassis or Traefik) is named.

## Dependencies

- Depends on: none
- Blocks: none

## References

- Epic story: [X-5 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
