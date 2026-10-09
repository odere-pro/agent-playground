---
title: "X-8: Event broker in Docker Compose and Kubernetes, with retention and consumer-lag alerts"
labels: ["story", "priority:P0", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W2 Chassis MVP"
index: 20
epic_id: X-8
depends_on: ["001 DEC-1", "016 H-9"]
blocks: ["021 H-20", "026 CH-4", "037 M-2", "040 D-0", "072 C-3"]
epic_refs: [D.1, H]
---

## Why

Pulled forward from Phase 8: the event port, the recorder (040 D-0), and the audit log (072 C-3) all need a running broker. It runs in Docker Compose for the local platform and on Kubernetes for the cloud. The broker is the one chosen in 001 DEC-1: NATS JetStream (recommended) or the company's Kafka.

## What

- The broker per DEC-1, added to the Docker Compose profile from 016 H-9, plus a Helm chart for Kubernetes. If DEC-1 picks the company's existing Kafka, this issue connects to it instead of running a new one.
- Streams or topics created from config for every event type in the D.3 catalog.
- Persistent storage, so events survive a broker restart.
- Retention per stream set in config, so events can be replayed to rebuild records and investigate incidents.
- Ordering by `run_id`: events for one run land in the same partition or subject.
- Consumer-lag metrics in Prometheus, with an alert rule per consumer.
- Each service's chassis, or its Dapr component, connects with the service's own broker credential, from secret references. Topic ACLs limit each service to the topics in its `spec.events`. Workload containers and `remote` pods get no broker credential ([ADR-001](../adr/001-chassis-delivery-model.md) hard requirement 1).
- The broker refuses a client that carries no credential.
- Status after PoC-4: Kafka runs in the PoC-4 scale stack only, with a PLAINTEXT listener and no auth. Any container on the network, a workload included, can publish to and read the result topics directly, around the chassis. Its controller listener (9093) binds every interface, topics are created automatically, and its root is writable. This issue owns, before production: SASL and topic ACLs per service, TLS, topics created from config with auto-create off, and the controller listener closed (`deploy/compose/SECURITY.md`, section 7). Which broker runs is still 001 DEC-1's: [ADR-004](../adr/004-events-through-a-broker-client.md) proposes a broker client in the chassis and leaves NATS JetStream or Kafka open.
- Status after PoC-5: Kafka runs on kind with SASL/SCRAM and no PLAINTEXT listener, the SCRAM users from `seed.sh` (user decision, 2026-10-09). H11 passed: from the workload container, no credential, an unknown user, and a guessed password each get the broker's error 58, and the chassis's own publish lands on `agents.task.completed.v1` (`test_poc05_kind_hardreq1.py::test_kafka_refuses_the_workload_without_the_chassis_credential`; [close runs](../../../pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md), section 3). This issue now keeps only topic ACLs per service, TLS, and the controller listener. The PoC-4 Compose Kafka is still PLAINTEXT with no auth (`deploy/compose/SECURITY.md`, section 8). The broker stays behind `EventPort`; a new broker is either a Dapr pub/sub component behind the `dapr` adapter or a broker adapter bound to `EventPortContract`, with no chassis change. Result events must not carry caller data on a shared broker until topic ACLs per service are in.

## Reuse

- **Use:** Kafka through Strimzi or a managed service (MSK, Confluent), or the NATS JetStream Helm chart. With Dapr, Kafka is the stable path.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The chassis broker adapter (019 H-17) and the event consumer (021 H-20).
- Default-deny egress and the negative tests from the workload container (026 CH-4).
- Dead-letter topics and retries with backoff (053 H-22).
- Other broker types (060 H-23).
- KEDA scaling on consumer lag (104 O-3).

## Acceptance criteria

- [ ] `docker compose up` starts the broker, and the echo agent's result events land in `agents.task.completed.v1`.
- [ ] The broker installs on a Kubernetes cluster from its Helm chart (suggested: a local test cluster).
- [ ] Every event type in the D.3 catalog has a stream or topic, created from config.
- [ ] Events survive a broker restart, and are dropped after the configured retention period.
- [ ] Consumer lag shows in Prometheus, and an alert fires when a stopped consumer's lag passes its threshold.
- [ ] Events with the same `run_id` are read in the order they were written.
- [ ] A client with no credential is refused, and the ACLs limit each service to the topics in its `spec.events`: a publish to another topic fails.

## Dependencies

- Depends on: [001 DEC-1](001-DEC-1-resolve-open-decisions.md), [016 H-9](016-H-9-local-debug-profile.md)
- Blocks: [021 H-20](021-H-20-event-consumer-adapter.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [037 M-2](037-M-2-eval-gate-ci.md), [040 D-0](040-D-0-recorder-agent.md), [072 C-3](072-C-3-audit-log-agent.md)

## References

- Epic story: [X-8 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [D.1](../slm-agent-platform-epic-v3.md#d1) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
