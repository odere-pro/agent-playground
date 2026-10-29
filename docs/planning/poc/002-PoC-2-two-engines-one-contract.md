---
title: "PoC-2: Two engines, one contract, two lanes: prove the chassis is framework-agnostic"
labels: ["poc", "priority:P0", "area:harness"]
milestone: "Agent MVP"
index: 2
iteration: PoC-2
timebox: "2 weeks"
depends_on: ["PoC-1"]
backlog_refs: ["008 H-14", "009 CH-1", "013 CH-2", "054 H-16"]
---

## Question

Can two very different frameworks run as workloads behind the same `handle` contract, over A2A in the `sidecar` lane and over A2A in memory in the chassis's tests, with no framework code in the chassis and no key in any workload? Does a workload that is not Python pass the same contract?

## Status

Done on 2026-10-01.

## What is left

Nothing.

## Where the rest is

- The evidence for every exit criterion is in [the PoC README](../../../pocs/poc-02-two-engines-one-contract/README.md).
- The full plan as it was (scope, demo, exit criteria) is in git: `git show 23ffbdc:docs/planning/poc/002-PoC-2-two-engines-one-contract.md`.
