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

In progress. 7 of 8 exit criteria have evidence; criterion 3 is flagged by the user's decision (2026-10-30). The optional kind run is done. CI is green on `main` at `6888e2c`: `check`, `poc09-scaffold`, `poc09-helm`.

## What is left

- **The trial by someone new to the code (exit criterion 6).** A person who did not build the chassis goes from `agentctl new` to a working demo in under a day. Steps: [the trial](../../../pocs/poc-09-agent-mvp-template/notes/2026-10-30-new-user-trial.md). A stand-in trial by a fresh AI agent found ten gaps, now fixed; it does not count.
- **The live demo, on the user's machine.** `pocs/poc-09-agent-mvp-template/demo/demo.sh --live` calls the Compose stack from the host: native, OpenAI, and MCP. Not run: `curl` is not allowed in the PoC-9 sessions.
- **A debugger attached to each container,** in the Compose `fake` profile.
- **The `local` Compose profile,** which needs a running LiteLLM.
- **Close.** Record the trial, tick criterion 6, set the README status to `done`.

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
