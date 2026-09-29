---
title: "H-17: Event port: one-way CloudEvents result events to the broker"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 19
epic_id: H-17
depends_on: ["001 DEC-1", "007 H-1", "009 CH-1"]
blocks: ["021 H-20", "023 C-5", "040 D-0", "043 A-3", "052 H-21", "072 C-3", "076 R-1"]
epic_refs: [B.4, D.1, D.2, R13]
---

## Why

Stateless agents never store records: they send a one-way result event, and a data agent stores it. This port is how results reach the recorder (040 D-0) and the audit log (072 C-3). Under [ADR-001](../adr/001-chassis-delivery-model.md), the workload never publishes itself. It streams its events to the chassis over A2A, and the chassis publishes the result event. This issue ships the first broker adapter, the one chosen in 001 DEC-1. The other adapters come in 060 H-23.

## What

- An `EventPort` interface in the chassis; the core never talks to a broker directly.
- The workload emits events in the chassis JSON event schema over A2A (009 CH-1). The chassis builds the CloudEvent and publishes it. The workload is given no broker credential and no Dapr API token.
- CloudEvents 1.0 events with the extensions `traceparent`, `idempotencykey`, `configversion`, and `modelroute`.
- Topic names in the form `<domain>.<entity>.<event>.v<major>`. The agent produces what `spec.events.produces` lists, such as `agents.task.completed.v1` and `agents.task.failed.v1`.
- A result payload with input, output, metrics, versions, and `idempotency_key` (B.4), plus an optional `source` field (which model produced the original).
- Payload JSON Schema files kept in the config store.
- `run_id` as the partition key when present, so events for one run keep their order.
- Switched on by the `result_events` module. Publishing never changes the response; a failed publish is retried and counted in metrics.
- The Dapr pub/sub component for the broker chosen in DEC-1 (see Reuse), plus an in-memory fake for tests.
- Dapr or a broker client in the chassis: the PoC track's PoC-4 tries both behind this port and recommends one, recorded in 001 DEC-1 (gap (c) in the [backlog plan](000-plan.md#adr-001-follow-ups)). suggested: the broker client, because Dapr is a third container in every pod, with its own localhost API to close and its own release train. If Dapr is kept, its API needs a token mounted into the chassis container only.

## Reuse

- **Use:** Dapr pub/sub. The chassis publishes through the pod's Dapr sidecar, and Dapr wraps the payload in a CloudEvents envelope. The CloudEvents Python SDK 2.x for the extensions (pin it; 2.x is new).
- **Build:** the `EventPort` over the Dapr publish API, payload schemas, extensions, and the `run_id` partition key through Dapr metadata (check per broker).
- **Scope change:** "the first broker adapter" becomes the Dapr pub/sub component for the broker chosen in DEC-1.
- **Watch:** Dapr's NATS JetStream component is beta. Kafka is stable. Dapr is a third container in the pod, and its localhost API can be reached by the workload: turn on Dapr API-token auth with the token in the chassis container only, or use a broker client in the chassis (see the gaps in 000-plan.md).
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Running the broker (020 X-8).
- Consuming events (021 H-20), and consumer retries and dead-letter topics (053 H-22).
- Filling `source` for the simplifier (043 A-3).
- The AsyncAPI spec (052 H-21) and other broker adapters (060 H-23).
- Broker credentials and topic ACLs per service (020 X-8), and the negative tests from the workload container with default-deny egress (026 CH-4).

## Acceptance criteria

- [ ] With `result_events` on, one echo call publishes one `agents.task.completed.v1` event, and a failed call publishes `agents.task.failed.v1`.
- [ ] Every event passes CloudEvents 1.0 validation and its payload schema, and carries all four extensions.
- [ ] The event's `idempotencykey` matches the call's key, and `traceparent` continues the call's trace.
- [ ] A broker outage does not fail the agent's response, and the failed publish shows in a metric.
- [ ] The core has no broker client import; switching to the in-memory fake needs config only.
- [ ] The DEC-1 adapter passes a publish test against a real broker started by the test.
- [ ] The Dapr adapter and the in-memory bus pass the same `EventPort` contract suite, and are picked by `spec.adapters.events`.
- [ ] The workload container is given no broker credential and no Dapr API token: neither is in its environment or file system. With Dapr, a publish call without the chassis's token is refused.
- [ ] A workload's events reach the broker only through the chassis, with the same payload in the `inprocess` and `sidecar` lanes.

## Dependencies

- Depends on: [001 DEC-1](001-DEC-1-resolve-open-decisions.md), [007 H-1](007-H-1-harness-library-ports-envelope.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [021 H-20](021-H-20-event-consumer-adapter.md), [023 C-5](023-C-5-ai-generated-marking.md), [040 D-0](040-D-0-recorder-agent.md), [043 A-3](043-A-3-result-events.md), [052 H-21](052-H-21-asyncapi-spec.md), [072 C-3](072-C-3-audit-log-agent.md), [076 R-1](076-R-1-registry-data-model-api.md)

## References

- Epic story: [H-17 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.4](../slm-agent-platform-epic-v3.md#b4) · [D.1](../slm-agent-platform-epic-v3.md#d1) · [D.2](../slm-agent-platform-epic-v3.md#d2) · [R13](../slm-agent-platform-epic-v3.md#r13)
- Backlog plan: [000-plan.md](000-plan.md)
