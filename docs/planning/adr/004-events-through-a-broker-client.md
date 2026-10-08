# ADR-004: Result events through a broker client in the chassis, not Dapr

- **Date:** 2026-10-01
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (gap (c), the cost table, hard requirement 1), [PoC plan](../poc/000-plan.md#decisions-for-the-epic-owner), [PoC-4](../poc/004-PoC-4-stateless-scalable.md), [001 DEC-1](../issues/001-DEC-1-resolve-open-decisions.md), [019 H-17](../issues/019-H-17-event-port.md), [020 X-8](../issues/020-X-8-event-broker.md), [060 H-23](../issues/060-H-23-broker-adapters.md), [contract v3](../../contracts/contract-v3.md), the PoC-4 note `pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md` (every number below, with its commands and raw files), and `deploy/compose/SECURITY.md` section 7

## Status

Proposed, 2026-10-01. Built and measured in PoC-4; waiting for the epic owner's acceptance. Decision 1 settles Dapr against a broker client. Decision 2 leaves the broker product open in 001 DEC-1, on purpose.

## Context

ADR-001 says only the chassis holds credentials, the broker's included, and that the workload never publishes. It left one choice open (gap (c)): do result events go out through Dapr pub/sub, a daprd sidecar per replica, or through a broker client inside the chassis? Both sit behind `EventPort`. PoC-4 built both and bound both to the same `EventPortContract` suite (8 cases), next to the in-memory bus.

How it was measured: one chassis and `echo-python` pair on Docker Desktop (macOS arm64, a 14-CPU VM, with other work running on the host), the fake model server, three paths picked by config only (`events: none`, `kafka`, `dapr`). Each path ran 3 times: 60 s at 10 RPS after a 10 s warm-up, `docker stats` every 2 s (16 samples per run). Both event paths published to the same Kafka.

Facts that shape the choice:

- **Delivery: both paths delivered every event.** Kafka 2,134 of 2,134 and Dapr 2,137 of 2,137 over three runs each, nothing on `agents.task.failed.v1`, 0 failed requests in every run.
- **Chassis CPU: no difference resolved at 10 RPS.** The ranges overlap: none 0.116 to 0.141 vCPU, Kafka 0.120 to 0.174, Dapr 0.132 to 0.181. The spread between runs of one path (up to 0.054 vCPU) is as large as the gap between paths. Differences under about 0.05 vCPU are within the run-to-run spread on this host and are not findings.
- **Chassis memory at 10 RPS:** none 161 to 162 MiB, Dapr 163 to 164, Kafka 164 to 194. The 194 MiB was the first run of the session, where every container was higher; it is not attributed to aiokafka, but it is the recorded range.
- **daprd, the third container per replica.** CPU 0.0024 to 0.0037 vCPU at 10 RPS. Memory 34 to 151 MiB at 10 RPS and 26 to 139 MiB idle, a 4x swing between runs with no known cause (the high run was the session's first daprd start). Image 263 MB. ADR-001's cost table leaves this container out.
- **Code: Dapr saved none.** In the marked sections (CloudEvents wire form, publish retries, delivery retries, dead-letter topic) the Kafka adapter has 78 lines and the Dapr adapter 101. Whole files: 241 and 271. Dapr adds 46 lines of component YAML (34 not comment or blank) and a 120-line Compose overlay. The Dapr CloudEvents section is longer because it undoes what daprd does to the event.
- **Contract: Dapr fails two `EventPortContract` cases**, kept as strict xfails in `packages/chassis/tests/integration/test_dapr_events_contract.py`:
  - `test_after_max_attempts_the_event_goes_to_the_dead_letter_topic`: daprd dead-letters the event as published, with no `deadletterreason` and no `deadletterattempts`, after its own `maxRetries + 1` deliveries from `resiliency.yaml`, whatever `subscribe(max_attempts=...)` says. The adapter's mismatch check runs only when `DAPR_MAX_RETRIES` is set, and no config sets it, so it never runs.
  - `test_two_groups_each_get_every_event`: one consumer group per app id. A second group needs a second daprd.
- **Behavior outside the suite.** daprd reads its subscriptions once, at start; a later subscription needs a daprd restart. daprd overwrites the event's `traceparent` with the publish request's trace context, and writes an all-zero one when there is none; the adapter has to repair it and filter the attributes daprd adds.
- **Security surface.** daprd shares the pair's network namespace, so the workload shares its loopback. Closing its API took 9 settings across 4 files and two tokens (`DAPR_API_TOKEN` for the chassis to daprd, `APP_API_TOKEN` for daprd to the chassis), held by the chassis and daprd only. With them, every non-health endpoint answered 401 to the workload. Two daprd ports still bind every interface with no auth, reachable from the workload and from every container on the network: metrics on 9090, and a random port (47901 in that run), inferred, not confirmed, to be the internal gRPC port for sidecar-to-sidecar calls, with mTLS off. Whether a call to it is forwarded to the chassis with the app token was not tested. The broker-client path adds no listener to the pair.
- **Shared gap.** Both paths use the same PLAINTEXT Kafka with no auth, which any container on the network can publish to directly (020 X-8).

Limits of the evidence: one host, one pair, one engine, 10 RPS, 60 s runs, 3 runs per path, with other agents working on the host. Publishing was not pushed to the rate where it would show in the chassis's CPU. On a cluster, daprd would come from the Dapr operator and injector, which was not measured. The images measured were built from a tree that other agents were editing.

Options considered:

| # | Question | Option | In short |
| - | -------- | ------ | -------- |
| A1 | How events leave the pod | A broker client in the chassis, behind `EventPort` | Passes the whole suite; no extra container, port, image, or token; broker auth and topics are the chassis's job |
| A2 | | Dapr pub/sub, a daprd sidecar per replica, behind `EventPort` | Broker swap by YAML; retries in daprd; fails two contract cases; a third container and a listener surface to close |
| B1 | The Dapr adapter after the decision | Keep it in the tree as a tested alternative behind the same port | One integration binding to keep green; a ready path if a Revisit condition is met |
| B2 | | Remove it | Less code and no daprd image to pin; rebuilding it later costs the PoC-4 work again |
| C1 | The broker product | Decide here | The evidence covers Kafka only; NATS JetStream was not run |
| C2 | | Leave it to 001 DEC-1, with the bar a new adapter must pass | The port and the suite make the product a swap |

## Decision

**We choose A1, B1, and C2.**

### Decision 1: events go through a broker client in the chassis

1. **Result events leave through a broker client inside the chassis, behind `EventPort`.** Not through Dapr pub/sub. The first broker client is the `kafka` adapter (`adapters/kafka/events.py`, aiokafka 0.14.0, pure Python), which passes all 8 `EventPortContract` cases against a real broker. The workload never publishes and holds no broker credential (ADR-001 hard requirement 1).
2. **No daprd in the pod.** 024 CH-3's library chart adds no Dapr container, annotation, or token. Each replica stays two containers: the chassis and the workload.
3. **The Dapr adapter stays in the tree as a tested alternative** (suggested). It stays selectable as `spec.adapters.events: dapr`, is the default in no profile, and keeps its integration binding with the two strict xfails, so a Dapr fix upstream turns them into failures that ask for a look. Its cost: one integration binding in `make test-integration`, the `deploy/compose/dapr/` component files and the scale overlay, and the daprd image digest to bump. Remove it if it breaks on an upgrade and no Revisit condition below is in sight.
4. **If Dapr is ever deployed,** these come first, and they are `platform-security`'s: the metrics port off or bound to 127.0.0.1 (suggested: `--enable-metrics=false` or `--metrics-listen-address 127.0.0.1`, not tried), and the internal gRPC port pinned and bound to 127.0.0.1 or protected by Sentry mTLS with access control (suggested: `--dapr-internal-grpc-port` and `--dapr-internal-grpc-listen-address`, not tried); and `DAPR_MAX_RETRIES` set from `max_attempts`, or the check dropped, since today it never runs.
5. **Profile defaults do not change.** `events: none` in `fake`, `local`, and `cloud` (suggested in contract v3), so result events stay opt-in per agent until 001 DEC-1 picks the broker. Then the `local` and `cloud` defaults become that broker's adapter.

### Decision 2: the broker product stays open in 001 DEC-1

6. **This ADR does not pick NATS JetStream or Kafka.** The epic recommends NATS JetStream. PoC-4 used Kafka for one reason: Dapr's Kafka component is stable (its NATS JetStream component is beta), so both paths could share one broker and be compared fairly. Nothing was run on NATS, so the evidence does not rank the two. 001 DEC-1 decides, with the owner of the broker and the retention needs.
7. **The product is a swap behind the port.** A broker change is a new adapter in `chassis/adapters/<broker>/`, loaded lazily, built from its own environment variables, and picked by `spec.adapters.events`. The core, the result publisher, `handle`, the A2A mapping, and the event schema do not change.
8. **What a NATS adapter must pass.** The same `EventPortContract`, all 8 cases, against a real NATS server started by the test, with no xfail. In particular (how NATS meets each is suggested, to be checked when the adapter is built):
   - publish returns only once the stream has stored the event (a JetStream publish ack), and a transient failure is retried, then raised as `PublishFailed`;
   - each `group` gets every event once (suggested: one durable consumer per group);
   - a handler that raises gets the event again, up to `max_attempts` deliveries in total, and then the event goes to `<topic>.dlq` with `deadletterreason` (the exception class name only) and `deadletterattempts` (suggested: `MaxDeliver` set from `max_attempts`, with the adapter publishing the dead letter itself, since JetStream has no dead-letter stream of its own);
   - events with one `partitionkey` reach one group in publish order, also across redeliveries;
   - every event validates against the CloudEvents 1.0 JSON Schema, with the same bytes on the wire as the Kafka adapter.

   It is then bound in `packages/chassis/tests/integration/` next to the Kafka binding. 060 H-23 builds the adapters DEC-1 does not pick first.

Why not the others:

- **A2** costs a third container per replica and a listener surface the tokens do not fully close, fails two contract cases, needs a restart for a new subscription, and rewrites the trace context, while saving no adapter code. What it offers, a broker swap by YAML and one sidecar shape for every language, the platform does not need today: one broker per deployment, and only the chassis publishes.
- **B2** saves little: the adapter is already written and bound. Keeping it means a Revisit condition can be tested in days, not rebuilt.
- **C1** would decide on evidence that covers one product.

## Consequences

Pros:

- The whole `EventPort` contract holds in production: dead-letter extensions, `max_attempts`, any number of groups, subscribe at any time, and the run's own `traceparent`.
- Each replica stays two containers. ADR-001's cost table stays complete: no daprd memory (up to about 150 MiB per replica measured, 34 MiB at the low end) to add per replica.
- No extra listener, image, or token in the pod. Hard requirement 1 has one credential to place for events, the broker's, in the chassis only.
- Retry, group, and dead-letter settings live in the chassis config, not in YAML outside it.

Cons:

- A broker client library ships in the chassis image (aiokafka today), loaded only when the `kafka` adapter is picked.
- The chassis owns retries and dead-lettering (78 lines in the Kafka adapter) and their bugs. One is open: a failed dead-letter publish ends that subscription's consumer task (contract v3, Known gaps; 019 H-17).
- Broker auth, TLS, ACLs, and topic creation are the chassis's and 020 X-8's job; Dapr would not have removed them, since both paths share the broker.
- A second broker product is a new adapter and a suite run, not a YAML change.
- A non-Python workload cannot publish events itself. That is ADR-001's rule anyway: the chassis publishes for every workload.

Contracts touched: `EventPort` keeps the shape in contract v3, and the decision picks which adapter is the production path. The envelope, the event schema, `handle`, the A2A mapping, and the `spec.adapters.events` names do not change, so `schema_version` does not move.

Costs across lanes: the publish happens in the chassis after the run, so it costs the same in every lane. `inprocess` and `sidecar` publish from the same chassis process. In the `sidecar` lane the pod stays two containers, where Dapr would have made it three. In the `remote` lane the chassis in front publishes and the remote workload gets no broker credential, the same as the other lanes; with Dapr, the chassis's pod would carry daprd.

## Revisit

From the PoC-4 note. Reopen decision 1 when any of these holds:

- The platform must run more than one broker product per deployment (Kafka plus a cloud bus, for example), so a broker swap by YAML is worth a sidecar.
- Non-Python workloads must publish or consume events themselves, outside the chassis. Then a language-neutral sidecar beats a client per language. This also means changing ADR-001.
- The cluster already runs the Dapr control plane (Sentry mTLS and the operator), which removes the open internal port and the token handling.
- A cluster measurement shows daprd steadily under about 40 MiB, and Dapr adds the dead-letter metadata and a per-subscription retry count upstream (the two strict xfails start to pass).
- Load near the one-pair ceiling shows the broker client costing the chassis materially more CPU than an HTTP post to daprd. 10 RPS could not price it.

Reopen decision 2 when 001 DEC-1 picks the broker: the chosen product's adapter must pass `EventPortContract` as in item 8 before it becomes a profile default.
