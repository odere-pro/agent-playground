---
title: "H-20: Event consumer adapter: agents started by CloudEvents, same core as REST"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 21
epic_id: H-20
depends_on: ["011 H-2", "019 H-17", "020 X-8"]
blocks: ["040 D-0", "053 H-22", "060 H-23", "072 C-3", "112 O-16"]
epic_refs: [D.1, Fig.2, R13]
---

## Why

Every agent can be started by an API call or a CloudEvents event, and runs the same core either way. It is moved into the chassis MVP because the recorder (040 D-0) consumes result events, and the Phase 0 done-when needs agents started by events. Dead-letter topics and backoff come later, in 053 H-22.

## What

- An event consumer as one more inbound adapter in the chassis, reading the event types in `spec.events.consumes`, such as `agents.task.requested.v1`.
- Events read by the chassis's broker client, never through the workload's port, so an event passes the pipeline like any other call ([ADR-004](../adr/004-events-through-a-broker-client.md)).
- Each CloudEvent mapped to the same `TaskInput` and `Context` as a REST call: `idempotencykey` to `idempotency_key`, `traceparent` to the trace, and `configversion` to the pinned config.
- The same `handle`, called through the engine connector (009 CH-1); the core and the workload cannot tell an event from a REST call.
- Request and reply: the result goes out as `agents.task.completed.v1` or `agents.task.failed.v1` with the request's correlation ID.
- An event acknowledged only after `handle` finishes, so a crash leads to redelivery (at-least-once).
- One consumer group per agent, so pods share the load and the agent scales by adding pods.
- If the `dapr` adapter is ever deployed, its API token stays in the chassis container only (gap (c) in the [backlog plan](000-plan.md#adr-001-follow-ups)).
- A contract test for the event path, added to the testing kit (015 H-8).

## Reuse

- **Use:** the broker client's consumer group in the chassis, behind `EventPort.subscribe` (ADR-004): the `kafka` adapter on aiokafka today. The chassis reads each event itself, never through the workload's port, and commits it after `handle` finishes.
- **Build:** the mapping from event to `TaskInput` and `Context`, and the reply events.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Idempotent consumers, retries with backoff, and dead-letter topics (053 H-22).
- The AsyncAPI spec (052 H-21).
- More broker adapters (060 H-23).
- Workflows started by events (112 O-16).

## Acceptance criteria

- [ ] An `agents.task.requested.v1` event starts the echo agent, and an `agents.task.completed.v1` event with the same correlation ID comes back.
- [ ] The same input gives the same output through REST and through an event, in the `inprocess` and `sidecar` lanes.
- [ ] Events reach the chassis port only. The workload port receives no event directly.
- [ ] The producer's trace continues through the agent via `traceparent`.
- [ ] Killing the agent during a call leads to the event being delivered again, not lost.
- [ ] Two pods in the same consumer group split the events, and each event is handled by one pod.
- [ ] The event path passes its contract test.
- [ ] The event path is tested offline with the in-memory bus, and the broker client adapter passes the same `EventPort` contract suite.

## Dependencies

- Depends on: [011 H-2](011-H-2-inbound-adapters.md), [019 H-17](019-H-17-event-port.md), [020 X-8](020-X-8-event-broker.md)
- Blocks: [040 D-0](040-D-0-recorder-agent.md), [053 H-22](053-H-22-event-reliability.md), [060 H-23](060-H-23-broker-adapters.md), [072 C-3](072-C-3-audit-log-agent.md), [112 O-16](112-O-16-event-triggers.md)

## References

- Epic story: [H-20 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [D.1](../slm-agent-platform-epic-v3.md#d1) · [Fig. 2](../slm-agent-platform-epic-v3.md#fig2) · [R13](../slm-agent-platform-epic-v3.md#r13)
- Backlog plan: [000-plan.md](000-plan.md)
