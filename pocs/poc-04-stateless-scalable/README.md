# PoC-4: Stateless and scalable: config from the store, idempotency, more replicas

Status: not started
Planning doc: [004-PoC-4-stateless-scalable.md](../../docs/planning/poc/004-PoC-4-stateless-scalable.md)
Time box: 1 week

## Question

Does the agent scale by adding replicas, with no state in the process and safe retries, on every engine? What does the chassis sidecar cost per replica? Do events go through Dapr or through a broker client in the chassis?

## Scope

- [ ] `StatePort` and `EventPort` are defined here, in the same shape as the PoC-1 ports, each with a fake and a contract suite.
- [ ] Real adapters behind their ports, each passing the same contract suite as its fake: `ConfigPort` (MinIO), `StatePort` (Valkey), and `EventPort` (see the Dapr decision below), next to the in-memory bus.
- [ ] The Dapr decision: Dapr pub/sub, or a broker client in the chassis behind `EventPort`. Both are tried behind the same port and suite. Measured: a third container's CPU and memory per replica, the work to close Dapr's localhost API to the workload, and the lines of code for CloudEvents, retries, and a dead-letter topic. suggested: the broker client. The result goes to [001 DEC-1](../../docs/planning/issues/001-DEC-1-resolve-open-decisions.md) and [019 H-17](../../docs/planning/issues/019-H-17-event-port.md).
- [ ] A liveness probe on the workload container (the A2A agent card GET), so a hung workload restarts the pod instead of leaving `/ready` false forever.
- [ ] Container roles, tried on a kind cluster: the workload as the Kubernetes native sidecar and the chassis as the main container. Native sidecars start first and stop last, so SIGTERM reaches the chassis first and it drains before the workload stops. Compared with the preStop delay below. The better one goes to [024 CH-3](../../docs/planning/issues/024-CH-3-helm-library-chart.md).
- [ ] Integration tests with testcontainers (MinIO, Valkey, and Kafka if Dapr is used).
- [ ] Config loader: the agent config comes from MinIO, is checked against a JSON Schema, and reloads without a restart.
- [ ] Idempotency: `idempotency_key` check, with an optional result cache in Valkey.
- [ ] Timeout and budget per call (`budget.max_tokens`, `timeout_ms`).
- [ ] Graceful shutdown with two containers: stop taking new requests, finish the ones in flight, then exit; `/ready` goes false first. suggested: on SIGTERM the workload finishes its in-flight `handle` calls before it exits, and both containers get a short preStop delay (gap (g) in the [backlog plan](../../docs/planning/issues/000-plan.md#adr-001-follow-ups)).
- [ ] Read-only root file system on both containers: the chassis and the workload.
- [ ] suggested: Traefik or nginx in front of N chassis-and-workload pairs in Docker Compose. Each pair scales as one unit, as a pod does.
- [ ] suggested: a Locust or k6 load test, run on plain Python and on each PoC-2 engine.
- [ ] The sidecar's cost per replica, measured under load: the local hop's extra latency (p50 and p95), and the chassis container's CPU and memory. They are compared with ADR-001's suggested figures (0.05–0.1 vCPU, 128–256 MiB, a 1–3 ms hop), as input to CPU right-sizing and to the ADR's Revisit rule.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] Swap drill: `StatePort` (in-memory ↔ Valkey) and, if used, `EventPort` (in-memory ↔ Dapr) switch by config only, with the same tests passing.
- [ ] A repeated call with the same key returns the same result on any replica.
- [ ] A killed replica loses no request that the client retries.
- [ ] Throughput grows with replicas for every engine (numbers recorded).
- [ ] Both containers run with a read-only root file system on every engine, or the engine is flagged.
- [ ] Stopping a pair under load fails no request in flight: the workload finishes its `handle` calls before it exits.
- [ ] A bad config is rejected by the schema, and the agent keeps the last good one.
- [ ] Engines that keep hidden state are listed, with a way to move the state out or a note to reject them.
- [ ] A hung workload restarts the pod, and the chosen container roles fail no request under a rolling restart.
- [ ] The Dapr decision is written down with the measurements, and 001 DEC-1 and 019 H-17 are updated.
- [ ] The sidecar's hop latency, CPU, and memory per replica are recorded next to ADR-001's figures. If they are far above, the ADR's Revisit rule goes to the epic owner.

## How to run

```bash
make test-poc POC=04
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
