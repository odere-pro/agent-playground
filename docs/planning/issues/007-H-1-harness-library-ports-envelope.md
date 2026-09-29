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
- One port per external dependency, as typed interfaces with no network code: `ModelPort`, `EnginePort`, `ToolPort`, `EventPort`, `StatePort`, `ConfigPort`, `TelemetryPort`, `FeedbackPort`, `EvaluatorPort`, `GuardrailPort`, `AuthPort`, `RegistryPort`, and `StorePort` (data agents). This extends the five ports in F.1, so any product behind a port can be swapped.
- An in-memory fake for every port.
- `EnginePort` is the engine connector interface: `setup`, `run` (a stream of chassis events), `close`, and `capabilities`. The lane is picked by `spec.engine.connector: inprocess | sidecar | remote`. This issue ships the interface and a scripted fake engine. The fake engine can also wrap any `handle` by a direct call, for the chassis's own unit tests. It is a test double, not a lane. The lanes come in 009 CH-1.
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
- Retry, fallback, budget, and schema check (017 H-4).
- Idempotency (018 H-18) and the event port (019 H-17).

## Acceptance criteria

- [ ] The core package imports no web framework or network code; an import-lint rule in CI enforces this.
- [ ] Every listed port exists as a typed interface with an in-memory fake, and each fake passes its port's contract suite.
- [ ] `make test` passes in CI from the first commit, with no network and no keys.
- [ ] An echo `handle` function streams `start`, `delta`, and `end` events through the fake engine's direct call, and the collector returns one complete envelope with the same output.
- [ ] The chassis image builds from the same commit and version as the package, and `chassis serve` starts it.
- [ ] A fixture event stream from a non-Python workload validates against the published schema.
- [ ] A fixture event stream in the previous major schema version validates too, and one in an unknown version is refused with a clear error.
- [ ] The envelope models accept the example envelope from the epic and reject an unknown `status`.
- [ ] Every response carries the `versions` field, including the chassis version.
- [ ] Unit tests cover the event model, the collector, and the envelope; the package builds and installs with uv.

## Dependencies

- Depends on: none
- Blocks: [008 H-14](008-H-14-one-agent-interface.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md), [010 H-12](010-H-12-config-loader.md), [011 H-2](011-H-2-inbound-adapters.md), [012 H-3](012-H-3-model-port.md), [014 H-7](014-H-7-observability.md), [018 H-18](018-H-18-idempotency.md), [019 H-17](019-H-17-event-port.md)

## References

- Epic story: [H-1 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [F.2](../slm-agent-platform-epic-v3.md#f2) · [G.1](../slm-agent-platform-epic-v3.md#g1)
- Backlog plan: [000-plan.md](000-plan.md)
