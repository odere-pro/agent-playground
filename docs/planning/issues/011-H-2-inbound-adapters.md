---
title: "H-2: Inbound adapters: native, OpenAI-compatible, Anthropic-compatible, with streaming"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:chassis", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 11
epic_id: H-2
depends_on: ["007 H-1", "008 H-14", "009 CH-1"]
blocks: ["015 H-8", "016 H-9", "021 H-20", "022 H-6", "023 C-5", "050 CH-5", "051 H-13", "061 H-5", "062 H-11"]
epic_refs: [F.2, G.1, R2]
---

## Why

Every agent must answer through native, OpenAI-compatible, and Anthropic-compatible APIs, streaming and complete. This is an epic acceptance criterion and part of the Phase 0 done-when, and it lets existing clients call any agent with no new SDK. This issue also owns `/health`, `/ready`, and graceful shutdown, which pools need to scale up and down safely. Under [ADR-001](../adr/001-chassis-delivery-model.md), the chassis owns the public port, so every request enters here, whatever lane the workload runs in.

## What

- Inbound adapters in the chassis: `POST /v1/run` (native envelope), `POST /v1/chat/completions` (OpenAI format), and `POST /v1/messages` (Anthropic format), each mapping to one canonical request.
- The canonical request goes through the engine connector, always over A2A: in memory in `inprocess`, on localhost in `sidecar` (009 CH-1), and over the network in `remote` (055 CH-6). The workload never knows which API was used.
- The chassis owns the public port. The workload port binds to localhost and is never in the Service.
- Streaming: internal events mapped to server-sent events in each format. Complete: the collector returns one response. The native stream sends one `event: <type>` / `data: <json>` frame per chassis event, then a final `response` frame carrying the collected envelope.
- Envelope fields taken from each format. For OpenAI and Anthropic calls, `idempotency_key` and the trace come from headers (suggested: `Idempotency-Key` and `traceparent`). How each format maps onto the canonical request is written in [contract v2](../../contracts/contract-v2.md) and proposed in [ADR-003](../adr/003-chat-formats-onto-the-canonical-request.md).
- Token counts from the `metrics` event returned in each format's `usage` field.
- `error` events mapped to each format's error shape, as HTTP with `x-should-retry`.
- Request limits in `spec.limits` (suggested: `max_tokens_max: 8000`, `timeout_ms_max: 120000`, `body_bytes_max: 1048576`, `messages_max: 256`), refused as 400 `limit_exceeded` or 413.
- A client that leaves a complete call stops the run ([contract v3](../../contracts/contract-v3.md#a-client-that-leaves-a-complete-call)).
- `GET /health` (process alive) and `GET /ready`, from a fixed list of checks: the lifespan is done, the config is loaded, the chassis is not draining, and, in the `sidecar` lane, the workload's localhost A2A endpoint answers ([contract v3](../../contracts/contract-v3.md#ready-and-health)).
- Graceful shutdown on SIGTERM, as [contract v3](../../contracts/contract-v3.md#shutdown) says: `/ready` goes 503 first, new calls are still served with `Connection: close`, in-flight runs finish, then the chassis exits. The workload stops after the chassis has drained. That order is a deploy rule (a native sidecar, or preStop sleeps).

## Reuse

- **Use:** FastAPI to host the HTTP adapters, and the request and response types from the official `openai` and `anthropic` Python SDKs, so no format schema is written by hand.
- **Build:** the three inbound adapters, each mapping to one canonical request; the event-to-SSE mapping per format; `/health`, `/ready`, and graceful shutdown. Managed runtimes are not hosted: they are reached through the `remote` lane (ADR-001).
- **Watch:** LiteLLM can translate Anthropic calls to the OpenAI format, but the chassis owns every input protocol, so LiteLLM is not used as the Anthropic front door.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- `/metrics` (014 H-7) and `/manifest` (051 H-13).
- Auth, scopes, and rate limits (022 H-6).
- The event consumer (021 H-20) and the public A2A endpoint (061 H-5).
- The service's own declared operations (050 CH-5).
- The Docker Compose file, which does not publish the workload port (016 H-9).
- The preStop delay and the rolling-restart test in Kubernetes (024 CH-3).
- A call to the workload port from outside the pod fails, and only the chassis port answers: moved to 024 CH-3 on 2026-11-06.
- A client that leaves an MCP call.
- ADR-003's acceptance, with four items it still has to decide: `stop`, `stop_sequences`, the OpenAI message `name`, and unknown Anthropic message keys. They are on the owner list, not here: the behavior is in the contract ([the PoC-3 debt note](../../../pocs/poc-03-one-interface-every-client/notes/2026-10-01-debt.md)).

## Acceptance criteria

Delivered by: PoC-3, PoC-4 (2026-11-06)

- [x] The echo agent gives the same output through all three endpoints, streaming and complete, in both the `inprocess` and the `sidecar` lanes. · Delivered in PoC-3: `pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_every_interface_gives_the_same_answer`, `::TestInterfaces::test_stream_and_complete_agree`; `pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_2_only_the_two_legitimate_groups_of_cells_skip` (49 of 64 cells run; the 15 skips are MCP stream and `echo-typescript` in `inprocess`).
- [x] Stock OpenAI and Anthropic client libraries read both streaming and complete responses with no changes. · Delivered in PoC-3: `pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_3_the_sdks_work_with_only_a_base_url_change`.
- [x] An `error` event returns the right error shape in each format. · Delivered in PoC-3: `pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_an_error_event_is_the_formats_error`; [contract v2](../../contracts/contract-v2.md#status-table).
- [x] Token counts appear in the `usage` field of OpenAI and Anthropic responses. · Delivered in PoC-3: `pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_every_cell_answers`.
- [x] `/ready` reports not ready until its fixed checks pass (lifespan done, config loaded, not draining, and the workload probe in `sidecar`); `/health` answers while the process runs. · Delivered in PoC-4: `packages/chassis/tests/test_readiness.py::test_ready_goes_false_after_failed_probes_and_health_stays_ok`, `::test_ready_says_starting_then_ready_then_draining`; [contract v3](../../contracts/contract-v3.md#ready-and-health).
- [x] In the `sidecar` lane, `/ready` reports not ready while the workload's A2A endpoint does not answer. · Delivered in PoC-4: `packages/chassis/tests/test_readiness.py::test_the_sidecar_probe_is_false_on_a_bad_status_or_a_dead_workload`; `pocs/poc-04-stateless-scalable/tests/test_liveness.py::test_ready_goes_false_when_the_workload_hangs_and_health_stays_ok`.
- [x] `/ready` goes 503 first; in-flight runs finish; the workload stops after the chassis has drained. · Delivered in PoC-4: `packages/chassis/tests/test_shutdown.py::test_sigterm_drains_the_in_flight_stream_after_ready_goes_false`; `pocs/poc-04-stateless-scalable/tests/test_graceful_shutdown.py::test_the_chassis_drains_in_flight_runs_and_ready_goes_false_first`; `pocs/poc-04-stateless-scalable/tests/test_workload_drain.py::test_workload_a2a_finishes_in_flight_handle_on_sigterm`; [contract v3](../../contracts/contract-v3.md#shutdown).

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md), [008 H-14](008-H-14-one-agent-interface.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [015 H-8](015-H-8-testing-kit.md), [016 H-9](016-H-9-local-debug-profile.md), [021 H-20](021-H-20-event-consumer-adapter.md), [022 H-6](022-H-6-security-middleware.md), [023 C-5](023-C-5-ai-generated-marking.md), [050 CH-5](050-CH-5-service-operations.md), [051 H-13](051-H-13-openapi-mcp-tools.md), [061 H-5](061-H-5-a2a-adapter.md), [062 H-11](062-H-11-adapter-package.md)

## References

- Epic story: [H-2 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.2](../slm-agent-platform-epic-v3.md#f2) · [G.1](../slm-agent-platform-epic-v3.md#g1) · [R2](../slm-agent-platform-epic-v3.md#r2)
- Backlog plan: [000-plan.md](000-plan.md)
