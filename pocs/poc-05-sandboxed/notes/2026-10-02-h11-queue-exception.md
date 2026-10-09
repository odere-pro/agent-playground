# H11: the queue exception for PoC-5 (2026-10-02)

**Update, 2026-10-09: H11 passed on kind.** The user brought Kafka with SASL on kind into the PoC-5 close. A login with no credential, an unknown user, and a guessed password each get the broker's error 58, and the chassis's own publish lands on `agents.task.completed.v1` (`pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_kafka_refuses_the_workload_without_the_chassis_credential`; [close runs](2026-10-09-close-runs.md), section 3). The exception below is closed. What remains is one accepted exception, at the end of this note. The rest is kept as written on 2026-10-02.

H11 is "the event broker refuses a call without the chassis's credential" (exit criterion 3, hard requirement 1 of [ADR-001](../../../docs/planning/adr/001-chassis-delivery-model.md)). PoC-5 records it as an **exception**, not a pass.

- **Owner:** `platform-security`.
- **Closes in:** [020 X-8](../../../docs/planning/issues/020-X-8-event-broker.md), the event broker with a credential per service and topic ACLs.
- **Plan:** [PoC-5 plan](../../../docs/plans/2026-10-02-poc-05-sandboxed.md), section 2.12 ("Kafka with SASL") and section 10, question 2.

## Why it is an exception

PoC-5 runs no broker. There is nothing on kind for H11 to probe.

- The PoC-5 agent configs keep `events: none`. `deploy/kind/poc05/agents/chassis/echo.yaml` (line 19) and `echo-remote.yaml` (line 18) both set it. `none` is also the `events` default in every profile (`packages/chassis/src/chassis/profiles.py`, `PROFILE_DEFAULTS`).
- `pocs/poc-05-sandboxed/tests/test_poc05_events_agnostic.py::test_poc05_agent_configs_run_no_broker` keeps that true on every commit.
- No manifest under `deploy/kind/poc05/` (outside the vendored agent-sandbox bundle) names Kafka or any other broker.

## The queue adapter is broker-agnostic

Decision 2026-10-02. The chassis does not pick a broker; `spec.adapters.events` does.

- The port is `EventPort` (`packages/chassis/src/chassis/ports/events.py`). The envelope is CloudEvents 1.0, structured mode, JSON, the same bytes on every broker.
- The `kafka` and `dapr` adapters bind `EventPortContract` (`packages/chassis/tests/integration/`). The `memory` fake binds it offline.
- RabbitMQ, GCP Pub/Sub, and AWS SNS/SQS fit as a Dapr pub/sub component behind the `dapr` adapter, with no chassis change.
- `chassis.ports.events` and `chassis.core` import no broker client. `test_poc05_events_agnostic.py` checks that, and that every events adapter binds the suite.
- Which broker runs is still 001 DEC-1's. [ADR-004](../../../docs/planning/adr/004-events-through-a-broker-client.md) is Proposed.

## H11 has no evidence anywhere today

No test, on any stack, shows a broker refusing a client without a credential.

- The PoC-4 Compose Kafka listens with no auth (020 X-8, "Status after PoC-4"). From `deploy/compose/docker-compose.scale.yaml`:

  ```
  KAFKA_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
  KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
  KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"
  ```

- So on that stack any container on the network, a workload included, can publish to and read the result topics around the chassis. The threat model lists H11 as `TODAY: OPEN, PLAINTEXT` (`2026-10-02-threat-model.md`, line 74).
- The plan's offline fallback ("no PLAINTEXT listener in the kind manifest, SASL users from a Secret") has nothing to check: there is no Kafka manifest on kind.

## What the exception allows, and what it does not

- PoC-5 may exit with H11 open, because no PoC-5 agent publishes or reads events.
- No PoC-5 config may set `spec.adapters.events` to anything but `none`. `test_poc05_events_agnostic.py` fails if one does.
- Result events must not carry caller data on a shared broker until H11 passes (threat model, section 4, "Recommendations").

## How it closes

When a broker reaches kind, H11 becomes one case in `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py` (task T21, not written yet), built like that file's Valkey and MinIO cases:

1. From the sidecar workload container of `agent-echo` (`kubectl exec`, the echo-python image's Python), connect to the broker with no credential, then with a guessed one. Expect a refusal each time. T10, the probe workload, is dropped, so this container is the caller. It shares the chassis's network namespace, so it has every edge the chassis has: a refusal here is a refusal by credential, not by NetworkPolicy.
2. The control, paired with step 1 in the same test: through the chassis, with the chassis's own credential from its Secret, a result event is published and lands on its topic.
3. The broker's manifest is checked offline too: no PLAINTEXT listener, auto-create off, credentials from a Secret that only the chassis pod mounts.

The case and its control close the exception. 020 X-8 also owns topic ACLs per service, TLS, and the controller listener.

## Accepted exception after the pass (2026-10-09)

Owner: 020 X-8 and `platform-security`. Accepted in the security review of the close ([review](2026-10-09-review-security-cluster.md), "Kafka with SASL on kind").

The SCRAM passwords are in the argv of `kafka-storage.sh format` for one short process at pod start. Only the broker container and node root can see them. They are hex, so a parse error cannot echo them. Fix: create the users with a credentialed admin step. SASL_PLAINTEXT is accepted on single-node kind only: SCRAM never sends the password, but event payloads cross in clear. There is no authorizer, so the chassis user can read, create, and delete any topic. TLS and per-service ACLs are 020 X-8.
