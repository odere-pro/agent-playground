---
title: "CH-1: Engine connectors `inprocess` and `sidecar` over A2A, plus the template A2A server that wraps `handle`"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 9
epic_id: CH-1
depends_on: ["007 H-1", "008 H-14"]
blocks: ["011 H-2", "013 CH-2", "014 H-7", "015 H-8", "016 H-9", "017 H-4", "018 H-18", "019 H-17", "024 CH-3", "055 CH-6"]
epic_refs: [F.1, F.2, G.1]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) decides that the chassis always runs in front, and the workload plugs in behind it through an engine connector. The default lane is `sidecar`: the workload runs in its own container in the same pod, and the chassis calls it over A2A on localhost. Tests and local runs use `inprocess`, so `make test` stays offline and fast. This issue builds both lanes. Every later issue can then run each contract case over both transports, which is the ADR's hard requirement 2. It comes right after 008 H-14, which fixes the `handle` contract.

## What

- The `EngineConnector` interface from 007 H-1: `setup`, `run` (a stream of chassis events), `close`, and `capabilities`. The lane is picked by `spec.engine.connector`.
- `inprocess`: the chassis loads the template A2A server in its own process and calls it over A2A in memory (an ASGI transport, no socket). It is the same wire contract as `sidecar`, without the socket. It is allowed only in the `fake` and `local` profiles, for the chassis's own tests and local runs. A workload's own tests run the chassis as a separate process in the `fake` profile instead (015 H-8), so the workload never installs the chassis package.
- `sidecar`: an A2A client (`a2a-sdk`) that sends the canonical request to the workload on localhost and streams its events back.
- One written mapping between chassis events and A2A task status updates and artifacts, used by both sides. PoC-1 wrote it: [contract v0](../../contracts/contract-v0.md#chassis-events-over-a2a).
- The template A2A server: a small server that wraps `handle` and serves A2A on localhost only. It ships with the service template (025 H-10). The echo workloads from 008 H-14 use it. Where it lives, and how a workload sees `handle` without installing the chassis package, is decided in [ADR-002](../adr/002-template-a2a-server-placement.md).
- `traceparent` and `ctx` (request ID, budget, idempotency key, config versions) passed over A2A.
- A timeout or a client disconnect cancels the A2A task.
- Open connections and events streamed straight through, so the local hop stays small. suggested: 1–3 ms, as in the ADR.
- A workload that does not answer, or sends an event that fails the schema, gives an `error` event and a clear status.

## Reuse

- **Use:** the official `a2a-sdk` 1.x: its client in the `sidecar` connector, and its server in the service template to wrap `handle`. The same client serves the `remote` lane later (055 CH-6).
- **Build:** the connector interface, the `inprocess` and `sidecar` connectors, the mapping between chassis events and A2A task updates, and the template A2A server.
- **Watch:** a2a-sdk 1.x is a new major version, so pin it.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The `remote` lane and the cloud auth adapters (055 CH-6). It reuses this A2A client.
- An OpenAI-compatible connector for workloads that cannot speak A2A. It is added only when such a workload exists.
- Model and tool calls from the workload (013 CH-2, 054 H-16).
- The public A2A endpoint for outside callers (061 H-5).
- Framework event mappings (058 CH-8).

## Acceptance criteria

- [ ] The same echo workload runs through `inprocess` and through `sidecar`, and returns the same envelope and event stream for the same input.
- [ ] Streaming works over A2A: the first `delta` reaches the client before the workload sends `end`.
- [ ] The template A2A server listens on localhost only. A call from another container or host fails.
- [ ] `inprocess` is refused outside the `fake` and `local` profiles.
- [ ] A timeout cancels the A2A task, and the workload sees the cancel.
- [ ] One trace ID links the logs of the chassis and the workload.
- [ ] `inprocess` opens no socket: the suite passes with networking disabled.
- [ ] The extra latency of the local hop is measured and recorded (p50 and p95), with the overhead per streamed delta and the time to first token.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md), [008 H-14](008-H-14-one-agent-interface.md)
- Blocks: [011 H-2](011-H-2-inbound-adapters.md), [013 CH-2](013-CH-2-outbound-model-proxy.md), [014 H-7](014-H-7-observability.md), [015 H-8](015-H-8-testing-kit.md), [016 H-9](016-H-9-local-debug-profile.md), [017 H-4](017-H-4-harness-features.md), [018 H-18](018-H-18-idempotency.md), [019 H-17](019-H-17-event-port.md), [024 CH-3](024-CH-3-helm-library-chart.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [F.2](../slm-agent-platform-epic-v3.md#f2) · [G.1](../slm-agent-platform-epic-v3.md#g1)
- Backlog plan: [000-plan.md](000-plan.md)
