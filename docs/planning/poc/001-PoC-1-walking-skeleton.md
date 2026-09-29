---
title: "PoC-1: Walking skeleton: testable from day 0, one request through the chassis and the router"
labels: ["poc", "priority:P0", "area:harness", "area:router", "area:testing"]
milestone: "Agent MVP"
index: 1
iteration: PoC-1
timebox: "1 week"
depends_on: []
backlog_refs: ["007 H-1", "009 CH-1", "015 H-8", "010 H-12", "011 H-2", "012 H-3", "002 G-1", "003 G-1b", "004 G-2"]
---

## Question

Is the chassis testable offline from day 0, with every dependency behind a swappable port? Does one request go end to end through the chassis and the router, streaming and complete, with tokens counted?

## Why

This is the smallest thing that proves the shape of the chassis. It sets the contract that every later iteration and every engine builds on: the envelope, the events, the `handle` function, and the ports. The test setup comes first, before any feature. Every later iteration then adds adapters to an offline suite that is already running, instead of adding tests after the fact. The router is in place from day one, so every model call is counted. How much to build is settled: [ADR-001](../adr/001-chassis-delivery-model.md) decides to build the chassis, and to reach managed runtimes through the `remote` lane (PoC-6).

## Scope

Day 0 (first 2 days, before any feature):

- [ ] Repo, `uv` project, CI pipeline, and `make test` running on the first commit.
- [ ] Port interfaces for the four dependencies the skeleton touches: `ModelPort`, `EnginePort`, `ConfigPort`, and `TelemetryPort`. The other ports are defined in the same shape by the iteration that adds their first adapter, so each contract is shaped by a real adapter, not guessed.
- [ ] An in-memory fake for each of the four ports.
- [ ] A contract-suite harness: one reusable pytest suite per port, parametrized over its adapters. For now it runs against the fakes only.
- [ ] A scripted fake model server: OpenAI-compatible `/v1/chat/completions`, streaming and complete, with tool calls, driven by a script file. Frameworks point their base URL at it in tests.
- [ ] `spec.adapters` in the config and three profiles: `fake`, `local`, `cloud`.
- [ ] Import-lint rule: no product SDK outside its adapter package.

Walking skeleton:

- [ ] The `chassis` package and the `chassis serve` launcher. The same package later becomes the one generic chassis image, with no business logic in it.
- [ ] Chassis core: the envelope, the event model (`start`, `delta`, `tool_call`, `metrics`, `end`, `error`), and `async def handle(input, ctx) -> AsyncIterator[Event]`.
- [ ] A collector that turns the event stream into one complete response.
- [ ] `POST /v1/run` with `stream: true` (server-sent events) and `stream: false`.
- [ ] `GET /health` and `GET /ready`.
- [ ] The template A2A server: a small a2a-sdk server that wraps `handle`, and one written mapping between chassis events and A2A task updates. It is the one wire contract from the first commit.
- [ ] The `inprocess` connector: the chassis loads that server in its own process and calls it over A2A in memory (an ASGI transport, no socket). ADR-001 allows it for the chassis's own tests and local runs. The `sidecar` lane, with the same server in its own container, comes in PoC-2.
- [ ] A plain-Python business logic plug-in: a simple simplifier prompt, served by the template A2A server.
- [ ] The first real adapter: `ModelPort` over OpenAI-compatible HTTP, pointed at LiteLLM. LiteLLM runs in Docker Compose, pinned by hash, with two named routes: `big-default` (an API model) and `local-small` (a small model on llama.cpp or vLLM).
- [ ] Token and cost counts per request in LiteLLM, tagged with the agent name.
- [ ] The response reports the `versions` used (config, prompt, model route).

## Reuse

- **Use:** FastAPI and Pydantic, the a2a-sdk 1.x server, the LiteLLM proxy, llama.cpp or vLLM for the local model, pytest, pytest-asyncio, and the httpx ASGI transport for the in-memory A2A calls.
- **Build:** the `chassis` package and `chassis serve`, the chassis core, the four ports and their fakes, the contract-suite harness, the fake model server, the template A2A server and its event mapping, the `inprocess` connector, `/v1/run`, and the collector.
- Details: [reuse analysis](010-reuse-analysis.md) · test design: [000-plan.md](000-plan.md#swappable-and-testable-from-day-0)

## Out of scope

- OpenAI, Anthropic, and MCP interfaces (PoC-3).
- Frameworks, the `sidecar` connector, and the chassis model proxy (PoC-2).
- The other eight ports. Each arrives with its first adapter: state and events in PoC-4, tools in PoC-5, auth, guardrails, the evaluator, and feedback in PoC-7, and the registry in the backlog.
- The `remote` lane (PoC-5) and managed runtimes (PoC-6).
- Real adapters for config, state, and events (PoC-4).
- Testcontainers integration tests (from PoC-4, when the first real stateful adapters arrive).

## Demo

`make test` passes on a laptop with the network off and no keys. Then `curl` streams a simplified text through the `local` profile and returns the same result as one complete response. The same request in the `fake` profile answers from the fake model server. Switching `model.route` from `big-default` to `local-small` gives the same response shape, and the LiteLLM logs show the tokens for both calls.

## Exit criteria

- [ ] `make test` passes offline with the `fake` profile: no network, no keys.
- [ ] Each of the four ports has an interface, a fake, and a contract suite that the fake passes.
- [ ] The plain-Python plug-in answers over A2A in memory, and the A2A messages validate against the a2a-sdk types.
- [ ] The real `ModelPort` adapter passes the same contract suite as its fake.
- [ ] `chassis serve` starts the chassis from config. The `chassis` package holds no business logic.
- [ ] `docker compose up` starts the chassis and the router with no manual steps.
- [ ] Streaming and complete responses carry the same output for the same input.
- [ ] Switching the model adapter (`fake` or `litellm`) or the model route needs a config change only.
- [ ] Token counts per request are visible in the router.
- [ ] Contract v0 is written down: the envelope, the events, `handle`, the mapping of chassis events to A2A, the ports, and a first draft of `EngineConnector`.

## Links

- Plan: [000-plan.md](000-plan.md) · Next: [PoC-2](002-PoC-2-two-engines-one-contract.md)
- Decision: [ADR-001](../adr/001-chassis-delivery-model.md)
- Backlog issues this previews: [007 H-1](../issues/007-H-1-harness-library-ports-envelope.md), [009 CH-1](../issues/009-CH-1-engine-connectors-a2a.md) (the template A2A server and `inprocess` only), [015 H-8](../issues/015-H-8-testing-kit.md), [010 H-12](../issues/010-H-12-config-loader.md) (`spec.adapters` only), [011 H-2](../issues/011-H-2-inbound-adapters.md) (native only), [012 H-3](../issues/012-H-3-model-port.md), [002 G-1](../issues/002-G-1-litellm-router.md), [003 G-1b](../issues/003-G-1b-named-model-routes.md), [004 G-2](../issues/004-G-2-token-cost-counting.md)
- Epic: [F.1](../slm-agent-platform-epic-v3.md#f1), [F.2](../slm-agent-platform-epic-v3.md#f2), [G.1](../slm-agent-platform-epic-v3.md#g1)
