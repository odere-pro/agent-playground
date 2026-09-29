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
- Streaming: internal events mapped to server-sent events in each format. Complete: the collector returns one response.
- Envelope fields taken from each format. For OpenAI and Anthropic calls, `idempotency_key` and the trace come from headers (suggested: `Idempotency-Key` and `traceparent`).
- Token counts from the `metrics` event returned in each format's `usage` field.
- `error` events mapped to each format's error shape.
- `GET /health` (process alive) and `GET /ready` (every registered readiness check passes, such as config loaded and model ready). One check is the connector: in the `sidecar` lane, the workload's localhost A2A endpoint must answer.
- Graceful shutdown on SIGTERM: stop taking new requests, finish in-flight ones, then exit. With two containers, no in-flight request may fail during a rolling restart. suggested: on SIGTERM, the workload finishes its in-flight `handle` calls before it exits, and both containers get a short preStop delay (gap (g) in the [backlog plan](000-plan.md#adr-001-follow-ups)).

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

## Acceptance criteria

- [ ] The echo agent gives the same output through all three endpoints, streaming and complete, in both the `inprocess` and the `sidecar` lanes.
- [ ] Stock OpenAI and Anthropic client libraries read both streaming and complete responses with no changes.
- [ ] An `error` event returns the right error shape in each format.
- [ ] Token counts appear in the `usage` field of OpenAI and Anthropic responses.
- [ ] `/ready` reports not ready until every registered readiness check passes (tested with a fake check); `/health` answers while the process runs.
- [ ] In the `sidecar` lane, `/ready` reports not ready while the workload's A2A endpoint does not answer.
- [ ] On SIGTERM, in-flight requests finish and new requests are refused. In the `sidecar` lane, this holds when both containers get SIGTERM at the same time.
- [ ] A call to the workload port from outside the pod fails. Only the chassis port answers.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md), [008 H-14](008-H-14-one-agent-interface.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [015 H-8](015-H-8-testing-kit.md), [016 H-9](016-H-9-local-debug-profile.md), [021 H-20](021-H-20-event-consumer-adapter.md), [022 H-6](022-H-6-security-middleware.md), [023 C-5](023-C-5-ai-generated-marking.md), [050 CH-5](050-CH-5-service-operations.md), [051 H-13](051-H-13-openapi-mcp-tools.md), [061 H-5](061-H-5-a2a-adapter.md), [062 H-11](062-H-11-adapter-package.md)

## References

- Epic story: [H-2 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.2](../slm-agent-platform-epic-v3.md#f2) · [G.1](../slm-agent-platform-epic-v3.md#g1) · [R2](../slm-agent-platform-epic-v3.md#r2)
- Backlog plan: [000-plan.md](000-plan.md)
