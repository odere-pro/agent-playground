---
title: "H-1: Chassis package and image: ports, `handle` contract, JSON event schema, envelope"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 7
epic_id: H-1
depends_on: []
blocks: ["008 H-14", "009 CH-1", "010 H-12", "011 H-2", "012 H-3", "014 H-7", "018 H-18", "019 H-17"]
epic_refs: [F.1, F.2, G.1]
---

## Why

Every service runs behind the chassis, so this is the base of the whole platform. It is also where the platform becomes testable from day 0: every external dependency gets a port and a fake before any real adapter exists. It fixes the ports, the `handle` contract, the chassis JSON event schema, and the envelope that every later issue builds on. Getting these right first avoids breaking changes in every service later. [ADR-001](../adr/001-chassis-delivery-model.md) item 1 sets the shape: one package and one generic image.

## What

- The `chassis` package (Python 3.12, uv, FastAPI, Pydantic) with a hexagonal layout: core, ports, adapters.
- The generic chassis image: the package plus the `chassis serve` launcher, with no business logic. Every service runs the same image.
- One repo, one CI pipeline, and one version number for both the package and the image.
- One port per external dependency, as typed interfaces with no network code: `ModelPort`, `ToolPort`, `EventPort`, `StatePort`, `ConfigPort`, `TelemetryPort`, `FeedbackPort`, `EvaluatorPort`, `GuardrailPort`, and `AuthPort`. Next to them sits the `EngineConnector` interface. It is a connector, not a port (contract spec section 1). This extends the five ports in F.1, so any product behind a port can be swapped.
- An in-memory fake for every port, and a scripted fake engine for `EngineConnector`.
- `EngineConnector` is the engine connector interface: `setup`, `run` (a stream of chassis events), `close`, and `capabilities`. The lane is picked by `spec.engine.connector: inprocess | sidecar | remote`. This issue ships the interface and a scripted fake engine. The fake engine can also wrap any `handle` by a direct call, for the chassis's own unit tests. It is a test double, not a lane. The lanes come in 009 CH-1.
- A contract-suite harness: one pytest suite per port, parametrized over its adapters, so every later real adapter must pass the same tests as the fake.
- An import-lint rule: no product SDK outside its adapter package.
- CI and `make test` from the first commit, running offline with no keys.
- The one contract every service implements: `async def handle(input: TaskInput, ctx: Context) -> AsyncIterator[Event]`.
- Published, versioned JSON Schemas for `TaskInput`, `Context`, and the events, so a workload in any language can implement the contract over A2A.
- The chassis JSON event schema, as versioned JSON Schema: `start`, `delta`, `tool_call`, `metrics`, `end`, `error`. How it travels over A2A is defined in 009 CH-1. The chassis accepts the current and the previous major version of the schema, so a workload built before a chassis release keeps working through a ring rollout.
- A collector that turns an event stream into one complete response.
- The native envelope as Pydantic models: `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version`, `input`, `context_ref`, `stream`, `budget`, `output`, `metrics`, `status` (`ok | retry | fallback | error`).
- A `versions` field in the response (chassis, config, prompt, and model route versions used), so every response reports its pinned versions. This is an addition to the envelope example in the epic, needed for the "pinned versions" property.
- `context_ref` is accepted and passed through, but not used yet.

## Reuse

- **Use:** uv, FastAPI, and Pydantic, as planned. JSON Schema (draft 2020-12) for the event model, so a workload in any language can check its events.
- **Build:** the `chassis` package, the generic chassis image with the `chassis serve` launcher, the ports, the `inprocess` connector, and the event schema.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- HTTP adapters and endpoints (011 H-2).
- The lanes: `inprocess` (A2A in memory) and `sidecar`, the template A2A server, and the mapping of chassis events to A2A (009 CH-1). The `remote` connector (055 CH-6).
- Framework event mappings (058 CH-8).
- Real model, store, or registry adapters (012 H-3 and later issues).
- `RegistryPort`, with the registry (076 R-1).
- `StorePort` for data agents, with the recorder agent (040 D-0).
- Retry, fallback, budget, and schema check (017 H-4).
- Idempotency (018 H-18) and the event port (019 H-17).

## Acceptance criteria

- [x] The core package imports no web framework or network code; an import-lint rule in CI enforces this. · Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (runs the import-linter contract "no product SDK or web framework in core and ports", `pyproject.toml`).
- [x] Every listed port exists as a typed interface with an in-memory fake, and each fake passes its port's contract suite. · Delivered in PoC-1, PoC-4, and PoC-7: `packages/chassis/tests/test_contracts.py::TestScriptedModel`, `::TestFakeEngineHandle`, `::TestInMemoryTools`, `::TestInMemoryConfig`, `::TestInMemoryTelemetry`; `packages/chassis/tests/test_events_contract.py::TestInMemoryBus`; `packages/chassis/tests/test_state_contract.py::TestInMemoryState`; `packages/chassis/tests/test_contracts_cross_cutting_ports.py::TestStaticTokens`, `::TestKeywordGuardrails`, `::TestScriptedEvaluator`, `::TestInMemoryFeedback`.
- [x] `make test` passes in CI from the first commit, with no network and no keys. · Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_day0.py::test_fake_profile_reaches_no_socket_when_the_offline_gate_runs`, `::test_fake_profile_sees_no_api_key_when_the_offline_gate_runs`; `scripts/tests/test_strip_secrets.py` (test `test_check_offline_strips_every_credential_suffix`).
- [x] An echo `handle` function streams `start`, `delta`, and `end` events through the fake engine's direct call, and the collector returns one complete envelope with the same output. · Delivered in PoC-1: `packages/chassis/tests/test_collector.py::test_echo_streams_and_collects_the_same_text`; `packages/chassis/tests/test_contracts.py::TestFakeEngineHandle::test_stream_starts_and_ends`.
- [ ] The chassis image builds from the same commit and version as the package, and `chassis serve` starts it.
- [x] A fixture event stream from a non-Python workload validates against the published schema. · Delivered in PoC-2: `packages/chassis/tests/test_schemas.py::test_events_from_a_typescript_workload_validate`; [contract v1](../../contracts/contract-v1.md#events-eventsv0json).
- [x] An event stream with an unknown `schema_version` is refused with a clear error. When `"1"` ships, `"0"` stays accepted ([contract v5](../../contracts/contract-v5.md#c3-what-counts-as-breaking-from-now-on)). · Delivered in PoC-1 and PoC-6: `packages/chassis/tests/test_events.py::test_unknown_schema_version_is_refused_with_a_clear_error`; `packages/chassis/tests/test_contract_freeze.py::test_both_servers_refuse_an_unknown_schema_version`. No previous major exists yet: the second half applies from `"1"`.
- [x] The envelope models accept the example envelope from the epic and reject an unknown `status`. · Delivered in PoC-1: `packages/chassis/tests/test_envelope.py::test_epic_example_response_is_accepted`, `::test_unknown_status_is_rejected`.
- [x] Every response carries the `versions` field, including the chassis version. · Delivered in PoC-1: `packages/chassis/tests/test_envelope.py::test_versions_is_required`; `packages/chassis/tests/test_server.py::test_complete_run_returns_a_response_with_versions`; [contract v1](../../contracts/contract-v1.md#versions).
- [ ] Unit tests cover the event model, the collector, and the envelope; the package builds and installs with uv.

## Dependencies

- Depends on: none
- Blocks: [008 H-14](008-H-14-one-agent-interface.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md), [010 H-12](010-H-12-config-loader.md), [011 H-2](011-H-2-inbound-adapters.md), [012 H-3](012-H-3-model-port.md), [014 H-7](014-H-7-observability.md), [018 H-18](018-H-18-idempotency.md), [019 H-17](019-H-17-event-port.md)

## References

- Epic story: [H-1 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [F.2](../slm-agent-platform-epic-v3.md#f2) · [G.1](../slm-agent-platform-epic-v3.md#g1)
- Backlog plan: [000-plan.md](000-plan.md)
