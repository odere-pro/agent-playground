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
- A result payload with input, output, metrics, versions, and the key's hash (B.4), plus a model-source field (which model produced the original).
- Decided in [contract v3](../../contracts/contract-v3.md#result-events): the key's sha256 hex, `key_hash`, is both the `idempotencykey` extension and the partition key, so events for one key keep their order. The payload schema is generated into `packages/chassis/schemas/task-result.v1.json`.
- `input` goes into a result event only when `spec.events.include_input` (suggested name) is true. Until topic ACLs per service are in (020 X-8), caller data must not go on a shared broker.
- Switched on by the `result_events` module. Publishing never changes the response; a failed publish is retried and counted in metrics. Result events publish in the background, never in the request's path.
- The broker client adapter for the broker chosen in DEC-1 (see Reuse), plus an in-memory fake for tests. Built in PoC-4: `EventPort` (`packages/chassis/src/chassis/ports/events.py`), the in-memory bus, and a `kafka` adapter on aiokafka, bound to `EventPortContract`.
- Dapr or a broker client in the chassis (gap (c) in the [backlog plan](000-plan.md#adr-001-follow-ups)): the broker client, decided in [ADR-004](../adr/004-events-through-a-broker-client.md), accepted 2026-10-08. Dapr would be a third container in every pod, with its own localhost API to close and its own release train. The `dapr` adapter stays as a tested alternative, the default in no profile. If Dapr is ever deployed, its API needs a token mounted into the chassis container only (ADR-004, item 4).

## Reuse

- **Use:** a broker client in the chassis, behind `EventPort` (ADR-004). The first one is the `kafka` adapter on aiokafka; the broker product is picked in DEC-1. The chassis builds the CloudEvents envelope itself; contract v3 uses no `cloudevents` SDK.
- **Build:** the `EventPort` adapter for the broker chosen in DEC-1: publish with retries, consumer groups, `max_attempts`, the dead-letter topic, payload schemas, extensions, and the `run_id` partition key.
- **Watch:** the chassis owns retries and dead-lettering and their bugs. Broker auth, TLS, ACLs, and topic creation are the chassis's and 020 X-8's job. The Dapr adapter stays in the tree as a tested alternative, the default in no profile.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Running the broker (020 X-8).
- Consuming events (021 H-20), and consumer retries and dead-letter topics (053 H-22). `spec.events.consume` is refused at load until event-triggered runs exist.
- A failed dead-letter publish that ends the subscription's consumer task: P-12 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md), with its box in 053 H-22.
- Filling the model-source field for the simplifier (043 A-3, parked).
- The AsyncAPI spec (052 H-21) and other broker adapters (060 H-23).
- Broker credentials and topic ACLs per service (020 X-8), and the negative tests from the workload container with default-deny egress (026 CH-4).

## Acceptance criteria

- [x] With `result_events` on, one echo call publishes one `agents.task.completed.v1` event, and a failed call publishes `agents.task.failed.v1`. · Delivered in PoC-4: `packages/chassis/tests/test_result_events.py::test_a_completed_run_publishes_one_valid_cloudevent`, `::test_a_failed_run_publishes_the_failed_type`
- [x] Every event passes CloudEvents 1.0 validation and its payload schema, and carries all four extensions. · Delivered by: `packages/chassis/tests/test_result_events_wire.py::test_every_result_event_passes_cloudevents_its_payload_schema_and_has_four_extensions` (completed and failed, against `packages/chassis/schemas/task-result.v1.json`)
- [x] The event's `idempotencykey` matches `key_hash` of the call's key ([contract v3](../../contracts/contract-v3.md#result-events)), and `traceparent` continues the call's trace. · Delivered by: `packages/chassis/tests/test_result_events_wire.py::test_the_idempotencykey_is_the_key_hash_and_the_traceparent_continues_the_call`
- [x] A broker outage does not fail the agent's response, and the failed publish shows in a metric. · Delivered in PoC-4: `packages/chassis/tests/test_result_events.py::test_a_failing_broker_does_not_fail_the_run` (`chassis.events.publish_failed`)
- [x] The core has no broker client import; switching to the in-memory fake needs config only. · Delivered in PoC-4, PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_events_agnostic.py::test_the_events_port_and_core_import_no_broker_client_in_source`, `pocs/poc-04-stateless-scalable/tests/test_swap_drill.py::test_event_port_swaps_by_config_only`
- [ ] The DEC-1 adapter passes a publish test against a real broker started by the test.
- [x] The broker client adapter and the in-memory bus pass the same `EventPort` contract suite, and are picked by `spec.adapters.events`. · Delivered in PoC-4: `packages/chassis/tests/test_events_contract.py::TestInMemoryBus`, `packages/chassis/tests/integration/test_kafka_events_contract.py::TestKafkaEvents`, `pocs/poc-04-stateless-scalable/tests/test_swap_drill.py::test_event_port_swaps_by_config_only`
- [x] The workload container is given no broker credential and no Dapr API token: neither is in its environment or file system. · Delivered in PoC-4, PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_kafka_static.py::test_the_secret_reaches_only_the_chassis_container_of_the_events_agent`, `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_kafka_refuses_the_workload_without_the_chassis_credential` (`pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md`); no Dapr in any profile (ADR-004)
- [x] A workload's events reach the broker only through the chassis, with the same payload in the `inprocess` and `sidecar` lanes. · Delivered by: `packages/chassis/tests/test_lane_result_events.py::test_inprocess_and_sidecar_publish_the_same_result_event_data`, `packages/chassis/tests/test_lane_result_events.py::test_the_workload_side_has_no_event_port_or_broker_config`
- [ ] A real result event carries `traceparent` and `modelroute`, checked on the published event.
- [ ] `input` is left out of a result event unless `spec.events.include_input` is true (suggested name).
- [x] One result event per call on the native, OpenAI, and Anthropic interfaces, and none for a replayed idempotent call. · Delivered by: `packages/chassis/tests/test_result_events_wire.py::test_one_result_event_per_call_and_none_for_a_replay` (`[native]`, `[openai]`, `[anthropic]`)
- [ ] The model-source field of a result event is named once and documented (suggested: `original_model`).

## Dependencies

- Depends on: [001 DEC-1](001-DEC-1-resolve-open-decisions.md), [007 H-1](007-H-1-harness-library-ports-envelope.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [021 H-20](021-H-20-event-consumer-adapter.md), [023 C-5](023-C-5-ai-generated-marking.md), [040 D-0](040-D-0-recorder-agent.md), [043 A-3](043-A-3-result-events.md), [052 H-21](052-H-21-asyncapi-spec.md), [072 C-3](072-C-3-audit-log-agent.md), [076 R-1](076-R-1-registry-data-model-api.md)

## References

- Epic story: [H-17 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.4](../slm-agent-platform-epic-v3.md#b4) · [D.1](../slm-agent-platform-epic-v3.md#d1) · [D.2](../slm-agent-platform-epic-v3.md#d2) · [R13](../slm-agent-platform-epic-v3.md#r13)
- Backlog plan: [000-plan.md](000-plan.md)
