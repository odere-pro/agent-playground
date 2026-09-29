---
title: "CH-5: Service operations: declared in config, routed by the chassis through the pipeline"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 50
epic_id: CH-5
depends_on: ["011 H-2", "010 H-12"]
blocks: ["051 H-13", "100 O-2"]
epic_refs: [G.1, B.5]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) item 3: the chassis owns the public port. Every request, including the service's own operations, enters through the chassis and passes the pipeline. A service declares its own operations, and the chassis routes them. This is what makes the chassis a standard for every microservice, not only for agents. The simplifier needs only `/v1/run`, so this issue waits until W5. It comes before the OpenAPI spec (051 H-13), which lists the operations.

## What

- `spec.operations` in the config schema (010 H-12). Each operation has a name, a method and path, an input schema, an output schema, and one of two answer types: one answer, or a stream.
- The chassis routes each declared operation through the pipeline (auth and scopes, limits, idempotency, telemetry). It sends the operation to the workload through the connector, as a canonical request that carries the operation name.
- The workload gets the operation name in its input, so one A2A server serves every operation.
- Refusals for what the chassis cannot carry, from the ADR's Cons: WebSockets, raw TCP, bodies over the size limit, and calls longer than the timeout.
- Long work returns an ID at once, and reports its result through a status operation or events.
- A clear error at start-up for an operation whose path clashes with a chassis path.

## Reuse

- **Use:** FastAPI routing and Pydantic models generated from each operation's JSON Schema.
- **Build:** the operation declarations in config, the router, and the refusals for what the chassis cannot carry.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The OpenAPI spec and the MCP tools for these operations (051 H-13).
- gRPC. If it is needed, it comes through A2A's gRPC transport.
- New protocol adapters, such as WebSockets. Each needs its own issue.

## Acceptance criteria

- [ ] A declared operation can be called on the chassis port, and passes auth, limits, and telemetry like `/v1/run`.
- [ ] An undeclared path returns 404, and the workload never sees it.
- [ ] Input that fails the operation's schema is refused before it reaches the workload.
- [ ] A streaming operation streams through the connector in both the `inprocess` and `sidecar` lanes.
- [ ] A declaration that clashes with a chassis path, or asks for WebSockets, fails at start-up.

## Dependencies

- Depends on: [011 H-2](011-H-2-inbound-adapters.md), [010 H-12](010-H-12-config-loader.md)
- Blocks: [051 H-13](051-H-13-openapi-mcp-tools.md), [100 O-2](100-O-2-temporal-checkpoints.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.1](../slm-agent-platform-epic-v3.md#g1) · [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
