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

Done on 2026-10-09. ADR-005 is accepted. Three exit criteria were closed with a flag.

## What is left

- **The sidecar hostile suite is not in CI (exit criterion 1).** The remote-lane kind suite runs in CI. The sidecar-lane kind suite runs only by hand (`deploy/kind/poc05/run.sh test`). Add a CI job for it.
- **MinIO through the chassis (exit criterion 3).** MinIO refuses the workload, as it should. But no chassis holds a MinIO key yet, so the allowed call through the chassis cannot be shown. Show it once a chassis gets a MinIO key.
- **The probe workload, T10 (exit criterion 8).** The in-pod probe workload is not built yet. It is a recorded exception owned by the user. It closes when [the T10 runbook](../../guides/poc-05-runbooks.md#testing-the-probe-workload-t10-after-it-is-built) passes.

## Where the rest is

- The evidence for every exit criterion is in [the PoC README](../../../pocs/poc-05-sandboxed/README.md).
- The full plan as it was (scope, demo, exit criteria) is in git: `git show 23ffbdc:docs/planning/poc/005-PoC-5-sandboxed.md`.
