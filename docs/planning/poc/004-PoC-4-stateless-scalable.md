---
title: "PoC-4: Stateless and scalable: config from the store, idempotency, more replicas"
labels: ["poc", "priority:P0", "area:harness"]
milestone: "Agent MVP"
index: 4
iteration: PoC-4
timebox: "1 week"
depends_on: ["PoC-2"]
backlog_refs: ["010 H-12", "018 H-18", "057 H-19", "017 H-4", "019 H-17", "024 CH-3"]
---

## Question

Does the agent scale by adding replicas, with no state in the process and safe retries, on every engine? What does the chassis sidecar cost per replica? Do events go through Dapr or through a broker client in the chassis?

## Status

Done on 2026-10-08. ADR-004 is accepted. One exit criterion was closed with a flag.

## What is left

- **Throughput at 4 pairs (exit criterion 4).** On the Mac, 4 pairs did not fit the Docker VM, so throughput did not grow for some engines. Run the load matrix again on a Linux host or with pinned CPUs. This moved to [122 X-5](../issues/122-X-5-load-tests.md).

## Where the rest is

- The evidence for every exit criterion is in [the PoC README](../../../pocs/poc-04-stateless-scalable/README.md).
- The full plan as it was (scope, demo, exit criteria) is in git: `git show 23ffbdc:docs/planning/poc/004-PoC-4-stateless-scalable.md`.
