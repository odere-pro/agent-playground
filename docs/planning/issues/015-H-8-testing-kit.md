---
title: "H-8: Testing kit: fake adapters, contract tests per API, record and replay"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 15
epic_id: H-8
depends_on: ["011 H-2", "012 H-3", "009 CH-1"]
blocks: ["025 H-10", "058 CH-8"]
epic_refs: [F.1]
---

## Why

Contract tests are part of the Phase 0 done-when, and every agent needs the same way to test itself. The kit gives each agent fake adapters, contract tests per API, and record and replay of model calls, so tests run fast, offline, and the same way in CI. It is also the home of [ADR-001](../adr/001-chassis-delivery-model.md)'s hard requirement 2: every contract case runs over A2A, as in production, and directly, as in tests. It comes after the connectors (009 CH-1), the inbound adapters (011 H-2), and the model port (012 H-3) because it tests and fakes them.

## What

- Fake adapters for every port from 007 H-1, with scripted answers and scripted errors.
- A scripted fake model server: OpenAI-compatible `/v1/chat/completions`, streaming and complete, with tool calls. Frameworks call models over HTTP with their own clients, so this is how every engine is tested offline.
- A fixture kit for business logic authors: fake model, fake tools, and event assertions.
- Integration fixtures with testcontainers (Valkey, Postgres, Kafka, MinIO) for real adapters.
- Schemathesis property tests generated from the OpenAPI spec.
- A contract test suite per API: `/v1/run`, `/v1/chat/completions`, and `/v1/messages`, streaming and complete, plus `/health` and `/ready`.
- Each case runs two ways: over A2A on localhost through the `sidecar` connector, and over A2A in memory through `inprocess` (hard requirement 2).
- One command to run the suite against any workload image, in any language (suggested: a pytest plugin). It starts the chassis in the `fake` profile as a separate process (suggested: `uvx chassis serve --profile fake`), so a workload's environment never installs the chassis package or its dependencies.
- A contract test that checks a framework's events map to the chassis JSON event schema. 058 CH-8 uses it for each framework mapping.
- Record and replay: record real model calls through the router once, then replay them in tests with no network.
- Recordings stripped of API keys and auth headers.
- A determinism check: the same input and config version give the same output on replay.
- Packaged with the chassis, so the template CI (025 H-10) runs it against the workload image.
- Built so later adapters can add their own contract tests.

## Out of scope

- Contract tests for events, MCP, and the public A2A endpoint, added by those issues (021 H-20, 051 H-13, 061 H-5). The A2A transport to workloads is in scope here.
- The CI job that runs a fake workload through the `remote` lane (055 CH-6).
- The framework event mappings, and a non-Python workload to run the suite against (058 CH-8).
- Running the suite in the template CI for each new service (025 H-10).
- The stateless check in CI (057 H-19).

## Acceptance criteria

- [ ] The echo agent passes the contract suite for all three APIs, streaming and complete, in both transports: over A2A on localhost through `sidecar`, and over A2A in memory through `inprocess`.
- [ ] A broken response shape (for example a missing `status`) fails the matching contract test in both transports.
- [ ] The event-mapping contract test fails on a fixture stream with an event that does not match the schema.
- [ ] A recorded model call replays with no network access and gives the same output.
- [ ] Recordings contain no API keys or auth headers.
- [ ] Each fake adapter can return a scripted error, so retry and fallback paths can be tested.
- [ ] The whole suite runs with one command against a given workload image, from an environment that has the kit but not the chassis package.
- [ ] The suite is a release gate for the chassis and for every new service (ADR-001 item 11). A chassis build that fails any case, in either transport, is not released.
- [ ] An engine that calls the model over HTTP runs offline against the fake model server, including streaming and a tool call.
- [ ] A real adapter and its fake pass the same port contract suite, one with testcontainers and one in memory.

## Dependencies

- Depends on: [011 H-2](011-H-2-inbound-adapters.md), [012 H-3](012-H-3-model-port.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [025 H-10](025-H-10-template-repo.md), [058 CH-8](058-CH-8-framework-event-mappings.md)

## References

- Epic story: [H-8 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1)
- Backlog plan: [000-plan.md](000-plan.md)
