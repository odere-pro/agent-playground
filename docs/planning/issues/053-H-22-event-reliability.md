---
title: "H-22: Event reliability: idempotent consumers, retries with backoff, dead-letter topic"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 53
epic_id: H-22
depends_on: ["018 H-18", "021 H-20"]
blocks: ["072 C-3"]
epic_refs: [D.1]
---

## Why

Delivery is at least once, so every consumer must handle duplicates, and a failing event must not block a stream or get lost. This issue adds retries with backoff and a dead-letter topic per agent, which the recorder (040 D-0) and every event-started agent need before real load. It delivers the "reliable events" goal of the harness completion wave.

## What

- Idempotent consumers: the event consumer adapter (021 H-20) drops duplicates by event `id` and `idempotencykey`, using the idempotency check in Valkey (018 H-18).
- Retries with backoff for failed handlers. Suggested config: `events.retry.max_attempts` and `events.retry.backoff_ms`.
- After the last attempt, the event goes to a dead-letter topic per agent, with the error and attempt count, and an alert fires.
- Suggested topic name: `agents.<agent>.deadletter.v1`, following the topic naming rule.
- Events that fail the schema check go straight to the dead-letter topic, with no retries.
- A replay command sends dead-letter events back to their original topic after a fix.
- The event port sets `run_id` as the partition key when present, so events for one run keep their order.
- Dead-letter topics are added to each agent's AsyncAPI spec (052 H-21).
- Metrics: duplicates, retries, and dead-letter count.

## Reuse

- **Use:** Dapr resiliency policies (retries with backoff) and a `deadLetterTopic` per subscription.
- **Build:** the duplicate drop (through 018 H-18), the replay command, and metrics.
- **Watch:** retry behavior differs slightly per broker. Test each one.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The other broker adapters (060 H-23).
- SLOs and routing alerts to on-call (117 X-3, 118 C-7).
- Idempotency for REST calls (018 H-18, already done).

## Acceptance criteria

- [ ] The same event delivered twice runs the core once. The second delivery is acknowledged and counted as a duplicate.
- [ ] A handler that fails twice and then succeeds is retried with growing delays and succeeds on the third attempt.
- [ ] An event that fails every attempt lands in the agent's dead-letter topic with the error and attempt count, and an alert fires.
- [ ] A schema-invalid event goes to the dead-letter topic with no retries.
- [ ] Replayed dead-letter events are processed once, and events that already succeeded are not processed again.
- [ ] Events with the same `run_id` are consumed in publish order in an ordering test.
- [ ] The recorder uses the same retries and dead-letter topic, so a failed record write ends in its dead-letter topic.

## Dependencies

- Depends on: [018 H-18](018-H-18-idempotency.md), [021 H-20](021-H-20-event-consumer-adapter.md)
- Blocks: [072 C-3](072-C-3-audit-log-agent.md)

## References

- Epic story: [H-22 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [D.1](../slm-agent-platform-epic-v3.md#d1)
- Backlog plan: [000-plan.md](000-plan.md)
