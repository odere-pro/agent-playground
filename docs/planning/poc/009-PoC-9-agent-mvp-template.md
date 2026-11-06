---
title: "PoC-9: Agent MVP: service template and agent factory CLI"
labels: ["poc", "priority:P0", "area:harness"]
milestone: "Agent MVP"
index: 9
iteration: PoC-9
timebox: "1 week"
depends_on: ["PoC-8"]
backlog_refs: ["025 H-10", "059 H-15", "024 CH-3", "058 CH-8"]
---

## Question

Can a developer take any business logic, pick a framework and a lane, and have a new agent scaffolded, running, and meeting the MVP bar in under a day?

## Status

Done on 2026-11-05. Two exit criteria were closed with a flag, by the user's decision. CI is green on `main` at `6888e2c`: `check`, `poc09-scaffold`, `poc09-helm`.

## What is left

- **The trial by someone new to the code (exit criterion 6, flagged).** The user closed it on 2026-11-05 with no trial by a person. A stand-in trial by a fresh AI agent found ten gaps, now fixed. A real trial is still worth running: [the steps](../../../pocs/poc-09-agent-mvp-template/notes/2026-10-30-new-user-trial.md).

The three checks on the user's machine ran on 2026-11-05: `demo.sh --live`, a debugger attach in the `fake` profile, and the `local` profile with a running LiteLLM ([machine checks](../../../pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md)). The attach found a wrong workload path mapping in the template, now fixed.

## Moved to the backlog

- A fake model the `fake` profile can script per case, so an agent can eval model-based logic offline (the kit's asks, item 6).
- Publish `workload-a2a`, `agent-testkit`, `fake-model-server`, and a pinned chassis image (025 H-10). Until then an agent reaches the platform through its `.platform` link.
- The chassis CLI asks: `serve --uds`, `--profile fake` with no config, and the bound proxy port reported.
- `agentctl`: `--trust`, the `orchestrator` and `data` kinds, `spec.modules`, the `governance` block, and a chart in the generated repo (059 H-15).
- The kit's remote lane: workloads reach the sidecar proxy, not the chassis's remote listener.
- On a cluster: traffic spread through the Service, the remote lane, and NetworkPolicy enforcement (024 CH-3).

## Where the rest is

- The evidence for every exit criterion is in [the PoC README](../../../pocs/poc-09-agent-mvp-template/README.md).
- The full plan as it was (scope, reuse, demo, exit criteria) is in git: `git show 6888e2c:docs/planning/poc/009-PoC-9-agent-mvp-template.md`.
