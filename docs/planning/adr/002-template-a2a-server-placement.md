# ADR-002: Where the template A2A server lives, and how a workload sees `handle`

- **Date:** 2026-09-29
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md), [PoC plan](../poc/000-plan.md#engine-connectors-agnostic-to-what-does-the-work), [PoC-1](../poc/001-PoC-1-walking-skeleton.md), [contract v0](../../contracts/contract-v0.md), [009 CH-1](../issues/009-CH-1-engine-connectors-a2a.md), [025 H-10](../issues/025-H-10-template-repo.md)

## Status

Accepted, 2026-09-29.

## Context

ADR-001 says a small A2A server from the service template wraps `handle`, and that the same server runs inside the chassis process in the `inprocess` lane. Two rules constrain where that server can live:

- A workload's environment never installs the chassis package (`packages/workloads/CLAUDE.md`). The sidecar exists so the workload's dependencies never mix with the chassis's.
- There is no SDK and no local API of our own (ADR-001, item 7). A2A is the one contract.

The server needs the chassis event and envelope shapes to validate what `handle` yields and to hand `handle` its `input` and `ctx`. In PoC-1 only the `inprocess` lane exists, so the server runs in the chassis process. In PoC-2 the same server runs in the workload's own container, where the chassis package is absent.

Facts that shape the choice:

- a2a-sdk 1.2 is a plain dependency: protobuf types, an `AgentExecutor` interface, a `TaskUpdater`, and Starlette routes. It has no opinion about our event schema.
- The chassis event and envelope shapes are published as JSON Schema (`packages/chassis/schemas/*.v0.json`). A schema is a contract, not a package; any language can validate against it.
- The `inprocess` connector must load a workload by a dotted path from config, so the workload must be importable in the chassis's test environment. The reverse must never hold.

Options considered:

| # | Option | In short |
| - | ------ | -------- |
| A | Server under `chassis.adapters.a2a`; the service template copies it in PoC-2 | The mapping is one pure module with no chassis import; the server validates with `parse_event` in the chassis and with `jsonschema` in the workload. The workload sees dicts |
| B | A small published package (`chassis-contract` or `a2a-handle-server`) that workloads install | Typed models on both sides, one copy of the code |
| C | Server in `packages/workloads/_template` from day 1; the chassis imports it for `inprocess` | One copy, on the workload side |
| D | The workload container runs the chassis image in an A2A-only mode | One copy, no template code at all |

## Decision

**We choose A.**

1. In PoC-1 the template A2A server lives in the chassis, under `chassis.adapters.a2a`:
   - `mapping.py`: the event and request mapping as pure functions over dicts. It imports a2a-sdk and nothing from `chassis`.
   - `server.py`: `HandleExecutor(AgentExecutor)`, `build_agent_card`, `build_app`. Its one chassis import is `chassis.core.events.parse_event`.
   - `inprocess.py`: the `inprocess` connector. It builds the app in the same process and calls it through `httpx.ASGITransport`.
2. The wire form of `handle` is dicts in and dicts out: `input` and `ctx` arrive as plain dicts shaped by `request.v0.json#input` and `context.v0.json`; each yielded dict must match `events.v0.json`. The server also accepts yielded objects with `model_dump(mode="json")`. Inside the chassis, the typed `Handle` alias and `echo` stay for the `FakeEngine` test double.
3. A workload is a `uv` workspace member so the chassis's tests can import it. It never depends on `chassis`. Config names it: `spec.engine.handle: "module:attribute"`. suggested: an import-linter contract that forbids `chassis` from every `packages/workloads/*` module.
4. In PoC-2 the service template (025 H-10) ships `mapping.py` and `server.py` into the workload as `a2a_server.py`, with `parse_event` replaced by `jsonschema` validation against the vendored `events.v0.json`. The chassis keeps its copy for `inprocess`. suggested: a CI check diffs the two copies of `mapping.py`, and the contract suite runs every case over both lanes (ADR-001, hard requirement 2).
5. The chassis package depends on `a2a-sdk[http-server]` (Starlette and sse-starlette), because `a2a.server.routes` needs them. `a2a` is imported only under `chassis.adapters`.

Why not the others:

- **B** is an SDK by another name. ADR-001 rejected that in draft 1 for a reason: a second artifact with its own version in every service. It also puts pydantic and our models into every workload's dependency set.
- **C** makes the chassis import workload code. It breaks the layering (`core` and `ports` below `adapters`, workloads outside the package) and puts business-logic scaffolding on the chassis's import path.
- **D** installs the chassis package into the workload's container, which is the dependency clash the sidecar lane exists to avoid, and it drags the chassis's public surface into a container that must listen on localhost only.

## Consequences

Pros:

- The workload holds no chassis code and no chassis dependency. A workload in another language does the same thing with the same schema.
- The mapping is one module with no chassis import, so the PoC-2 copy is a file copy, not a port.
- `inprocess` and `sidecar` exercise the same mapping code, which is what hard requirement 2 needs.

Cons:

- Two copies of `mapping.py` after PoC-2, kept equal by a CI diff. This is the price of no SDK.
- Python workloads lose typed `input` and `ctx`. A workload that wants types generates them from the schemas (suggested: `datamodel-codegen`) or writes `TypedDict`s.
- `chassis.core.handle.Handle` (models) and the wire form (dicts) are two signatures for one contract. The typed one is internal to the chassis.

## Revisit

- If the two copies of `mapping.py` drift more than once, or a third language needs the server, replace the copy with a generated file from one source, or reopen option B.
- If PoC-2's per-delta measurement makes the A2A wrapper too costly (ADR-001, Revisit), the mapping module is the only thing that changes.
