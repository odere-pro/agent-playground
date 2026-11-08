---
title: "PoC-5: Sandboxed: the remote lane and the trust rule"
labels: ["poc", "priority:P0", "area:harness", "area:security"]
milestone: "Agent MVP"
index: 5
iteration: PoC-5
timebox: "3 weeks"
depends_on: ["PoC-2"]
backlog_refs: ["022 H-6", "054 H-16", "026 CH-4", "055 CH-6"]
---

## Question

Can untrusted code, including code that a framework writes and runs, run in the `remote` lane without reaching secrets, the internet, the disk, or tools it is not allowed to use? And does a workload in the `sidecar` lane reach internal services only through the chassis?

## Status

Done on 2026-10-09. ADR-005 is accepted. Three exit criteria were closed with a flag. Criterion 3's flag was lifted on 2026-11-08 (MinIO through the chassis: #92, #100, remote-lane run 42503042654). Criterion 1's flag was lifted on 2026-11-06, when the sidecar hostile suite (sidecar controls, hard requirement 1 with the broker, the tool gateway, admission) joined the remote-lane CI job (`run.sh test-remote` after `run.sh kafka`; [the README](../../../pocs/poc-05-sandboxed/README.md) names the run).

## What is left

- **The probe workload, T10 (exit criterion 8).** The in-pod probe workload is not built yet. It is a recorded exception owned by the user. It closes when [the T10 runbook](../../guides/poc-05-runbooks.md#testing-the-probe-workload-t10-after-it-is-built) passes.

## Where the rest is

- The evidence for every exit criterion is in [the PoC README](../../../pocs/poc-05-sandboxed/README.md).
- The full plan as it was (scope, demo, exit criteria) is in git: `git show 23ffbdc:docs/planning/poc/005-PoC-5-sandboxed.md`.
