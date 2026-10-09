---
title: "H-23: More broker adapters behind the event port: Kafka, AWS, GCP"
labels: ["story", "priority:P2", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 60
epic_id: H-23
depends_on: ["021 H-20"]
blocks: []
epic_refs: [D.1, H]
---

## Why

The broker sits behind a port, so the platform can run with any broker and on any cloud. The first adapter ships in 019 H-17 (the broker chosen in DEC-1); this issue adds the rest. It is P2, because one broker is enough for every epic acceptance criterion.

## What

- Broker client adapters ([ADR-004](../adr/004-events-through-a-broker-client.md), see Reuse) behind the event port and the event consumer adapter, for the brokers not chosen in DEC-1, from this list: NATS JetStream, Kafka (Strimzi, Redpanda, or managed), AWS (SNS/SQS, EventBridge), and GCP Pub/Sub.
- The same CloudEvents mapping and extensions on every broker.
- The broker is picked by config only (suggested key: `events.broker`), with no change to agent code.
- The `run_id` partition key maps to each broker's ordering feature, for example the Kafka message key or the Pub/Sub ordering key.
- Retries and dead-letter topics (053 H-22) keep the same behavior, using the broker's own dead-letter feature where it has one.
- One shared contract test suite runs against every adapter.
- Suggested: local emulators in CI (Redpanda for Kafka, LocalStack for AWS, the Pub/Sub emulator for GCP).
- Status after PoC-5: a broker added here must pass the whole `EventPortContract` against a real broker, with no xfail ([ADR-004](../adr/004-events-through-a-broker-client.md), item 8). `chassis.core` and `chassis.ports.events` import no broker client (`pocs/poc-05-sandboxed/tests/test_poc05_events_agnostic.py`).

## Reuse

- **Use:** one broker client per broker behind `EventPort` (ADR-004): aiokafka for Kafka; suggested: nats-py for NATS JetStream, and the cloud SDKs for SNS/SQS and Pub/Sub. The `dapr` adapter stays as a tested alternative that reaches other brokers through Dapr components, the default in no profile.
- **Build:** one adapter per broker, each bound to `EventPortContract`, and the shared contract suite with local emulators.
- **Watch:** each adapter must pass the whole suite against a real broker with no xfail (ADR-004, item 8). Broker auth (SASL or the cloud's IAM) is the adapter's, with 020 X-8.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Running the chosen broker in Docker Compose and Kubernetes (020 X-8).
- Managed broker setup in Terraform (038 X-1a, 116 X-1b).
- New event types.

## Acceptance criteria

- [ ] Each new broker adapter passes the same contract suite as the first one: publish, consume, duplicate drop, retry, dead-letter, and `run_id` ordering.
- [ ] The echo agent switches broker by config only and passes its event tests on each broker.
- [ ] CloudEvents attributes and extensions survive a round trip on every broker.
- [ ] The contract suite runs in CI for every adapter.
- [ ] A short table documents each broker's limits: message size, ordering, and retention.

## Dependencies

- Depends on: [021 H-20](021-H-20-event-consumer-adapter.md)
- Blocks: none

## References

- Epic story: [H-23 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [D.1](../slm-agent-platform-epic-v3.md#d1) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
