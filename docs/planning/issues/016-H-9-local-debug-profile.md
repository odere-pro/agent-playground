---
title: "H-9: Local debug profile: Docker Compose, hot reload, debugger port"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 16
epic_id: H-9
depends_on: ["010 H-12", "011 H-2", "014 H-7", "009 CH-1"]
blocks: ["018 H-18", "020 X-8", "024 CH-3", "025 H-10", "084 R-3"]
epic_refs: [G.4]
---

## Why

The whole platform must run locally with `docker compose up`; this is an epic acceptance criterion. This profile gives each agent a fast inner loop, with hot reload and a debugger port. Under [ADR-001](../adr/001-chassis-delivery-model.md), each agent runs locally as it does in the cluster: the chassis in front, and the workload behind it in its own container. It is also the Compose base that Valkey (018 H-18), the event broker (020 X-8), and the registry's Docker watcher (084 R-3) add to.

## What

- A Docker Compose file with two containers per agent, the chassis and the workload, sharing one network namespace (suggested: `network_mode: "service:<chassis>"` on the workload). The workload's A2A port is never published on the host. Plus shared services: MinIO (config store), the LiteLLM router, Postgres 17 with pgvector, and the observability services from 014 H-7.
- A seed step that loads the example agent config and its schema into MinIO.
- A debug profile: the workload's source mounted into its container, hot reload on code change in the workload, and a debugger port on each container (suggested: uvicorn reload and debugpy).
- Local models: vLLM with a GPU, or llama.cpp on CPU, picked by a Compose profile.
- A `.env.example` that lists every variable, with no real secrets. Secrets from `.env` go only to the chassis container.
- A short guide: start, stop, attach a debugger to each container, and switch the model profile.

## Out of scope

- Valkey and the event broker (018 H-18, 020 X-8).
- The Helm library chart (024 CH-3) and the Kubernetes deploy (025 H-10).
- The registry's Docker watcher (084 R-3).

## Acceptance criteria

- [ ] `docker compose up` starts the chassis and the echo workload, MinIO, the router, and Postgres from a clean checkout.
- [ ] The workload's A2A port is not published on the host.
- [ ] The chassis reads the config from MinIO and answers on `/v1/run` through the echo workload.
- [ ] A code change in the workload is live without a rebuild or a restart of the stack.
- [ ] A debugger attaches to each container on its documented port and stops at a breakpoint.
- [ ] One trace ID links the logs of both containers.
- [ ] Secrets from `.env` are set only in the chassis container, checked from inside the workload container.
- [ ] Switching between the llama.cpp (CPU) and vLLM (GPU) profiles needs only a profile flag.
- [ ] No real secret is in the repo.

## Dependencies

- Depends on: [010 H-12](010-H-12-config-loader.md), [011 H-2](011-H-2-inbound-adapters.md), [014 H-7](014-H-7-observability.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [018 H-18](018-H-18-idempotency.md), [020 X-8](020-X-8-event-broker.md), [024 CH-3](024-CH-3-helm-library-chart.md), [025 H-10](025-H-10-template-repo.md), [084 R-3](084-R-3-docker-watcher.md)

## References

- Epic story: [H-9 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
