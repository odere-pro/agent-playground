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

- A Docker Compose file in each generated repo with two containers per agent, the chassis and the workload, sharing one network namespace (suggested: `network_mode: "service:<chassis>"` on the workload). The workload's A2A port is never published on the host. The template stack mounts the config as a file and calls an outside LiteLLM router.
- The PoC-4 Compose scale stack reads the config from MinIO, through the echo workload.
- A debug profile: the workload's source mounted into its container, hot reload on code change in the workload, and a debugger port on each container (suggested: uvicorn reload and debugpy).
- A `.env.example` that lists every variable, with no real secrets. Secrets from `.env` go only to the chassis container.
- A short guide: start, stop, and attach a debugger to each container.

## Out of scope

- Valkey and the event broker (018 H-18, 020 X-8).
- The Helm library chart (024 CH-3) and the Kubernetes deploy (025 H-10).
- The registry's Docker watcher (084 R-3).
- Postgres with pgvector: added by the first issue that needs it.
- Local models picked by a Compose profile, vLLM with a GPU or llama.cpp on CPU. Parked on 2026-11-06 (product plan; the SLM track). Only llama.cpp exists today (`deploy/compose/docker-compose.local.yaml`).

## Acceptance criteria

- [ ] `docker compose up` from a generated repo starts the chassis and the workload.
- [x] The workload's A2A port is not published on the host. · Delivered in PoC-9: `pocs/poc-09-agent-mvp-template/tests/test_poc09_template_ops.py::test_workload_is_hardened_keyless_and_unpublished`, `::test_every_published_port_is_on_loopback_and_only_on_the_chassis`.
- [x] The chassis reads the config from MinIO and answers on `/v1/run` through the echo workload. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md` section 4 (the Compose scale stack, through the echo workload).
- [ ] A code change in the workload is live without a rebuild or a restart of the stack.
- [x] A debugger attaches to each container on its documented port and stops at a breakpoint. · Delivered in PoC-9: `pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md` section 2 (breakpoints on 5678 and 5679); `pocs/poc-09-agent-mvp-template/tests/test_poc09_template_ops.py::test_a_debugger_is_attached_to_each_container`.
- [x] One trace ID links the logs of both containers. · Delivered in PoC-8: `pocs/poc-08-cross-cutting-build/tests/test_poc08_workload_logs.py::test_the_workload_logs_json_with_the_chassis_runs_trace_id`, `pocs/poc-08-cross-cutting-build/tests/test_poc08_c3_trace.py::test_every_call_has_metrics_and_json_logs_with_the_trace_id`. The workload runs as a thread in those tests, not as its own container.
- [x] Secrets from `.env` are set only in the chassis container, checked from inside the workload container. · Delivered in PoC-2 and PoC-9: `pocs/poc-02-two-engines-one-contract/demo/2026-10-01-demo-sidecar.md` ("key-like variables in the workload: none"); `pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md` section 3.
- [x] No real secret is in the repo. · Delivered in PoC-8: `scripts/tests/test_check_secrets.py` (test `test_check_secrets_fails_when_the_hook_finds_a_new_secret`); CI job `lint-extra` in `.github/workflows/ci.yml` runs `scripts/check_secrets.sh` on every push.

## Dependencies

- Depends on: [010 H-12](010-H-12-config-loader.md), [011 H-2](011-H-2-inbound-adapters.md), [014 H-7](014-H-7-observability.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [018 H-18](018-H-18-idempotency.md), [020 X-8](020-X-8-event-broker.md), [024 CH-3](024-CH-3-helm-library-chart.md), [025 H-10](025-H-10-template-repo.md), [084 R-3](084-R-3-docker-watcher.md)

## References

- Epic story: [H-9 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
