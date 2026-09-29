# PoC-1: Walking skeleton: testable from day 0, one request through the chassis and the router

Status: in progress
Planning doc: [001-PoC-1-walking-skeleton.md](../../docs/planning/poc/001-PoC-1-walking-skeleton.md)
Time box: 1 week

## Question

Is the chassis testable offline from day 0, with every dependency behind a swappable port? Does one request go end to end through the chassis and the router, streaming and complete, with tokens counted?

## Scope

Day 0 (first 2 days, before any feature):

- [x] Repo, `uv` project, CI pipeline, and `make test` running on the first commit.
- [x] Port interfaces for the four dependencies the skeleton touches: `ModelPort`, `EnginePort`, `ConfigPort`, and `TelemetryPort`. The other ports are defined in the same shape by the iteration that adds their first adapter, so each contract is shaped by a real adapter, not guessed.
- [x] An in-memory fake for each of the four ports.
- [x] A contract-suite harness: one reusable pytest suite per port, parametrized over its adapters. For now it runs against the fakes only.
- [x] A scripted fake model server: OpenAI-compatible `/v1/chat/completions`, streaming and complete, with tool calls, driven by a script file. Frameworks point their base URL at it in tests.
- [x] `spec.adapters` in the config and three profiles: `fake`, `local`, `cloud`.
- [x] Import-lint rule: no product SDK outside its adapter package.

Walking skeleton (core and collector landed with day 0, evidence `packages/chassis/tests/test_collector.py`):

- [ ] The `chassis` package and the `chassis serve` launcher. The same package later becomes the one generic chassis image, with no business logic in it.
- [x] Chassis core: the envelope, the event model (`start`, `delta`, `tool_call`, `metrics`, `end`, `error`), and `async def handle(input, ctx) -> AsyncIterator[Event]`.
- [x] A collector that turns the event stream into one complete response.
- [ ] `POST /v1/run` with `stream: true` (server-sent events) and `stream: false`.
- [ ] `GET /health` and `GET /ready`.
- [ ] The template A2A server: a small a2a-sdk server that wraps `handle`, and one written mapping between chassis events and A2A task updates. It is the one wire contract from the first commit.
- [ ] The `inprocess` connector: the chassis loads that server in its own process and calls it over A2A in memory (an ASGI transport, no socket). ADR-001 allows it for the chassis's own tests and local runs. The `sidecar` lane, with the same server in its own container, comes in PoC-2.
- [ ] A plain-Python business logic plug-in: a simple simplifier prompt, served by the template A2A server.
- [ ] The first real adapter: `ModelPort` over OpenAI-compatible HTTP, pointed at LiteLLM. LiteLLM runs in Docker Compose, pinned by hash, with two named routes: `big-default` (an API model) and `local-small` (a small model on llama.cpp or vLLM).
- [ ] Token and cost counts per request in LiteLLM, tagged with the agent name.
- [ ] The response reports the `versions` used (config, prompt, model route).

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [x] `make test` passes offline with the `fake` profile: no network, no keys. · evidence: `pocs/poc-01-walking-skeleton/tests/test_day0.py`, `packages/chassis/tests/test_contracts.py`
- [x] Each of the four ports has an interface, a fake, and a contract suite that the fake passes. · evidence: `pocs/poc-01-walking-skeleton/tests/test_day0.py`, `packages/chassis/tests/test_contracts.py`
- [ ] The plain-Python plug-in answers over A2A in memory, and the A2A messages validate against the a2a-sdk types.
- [ ] The real `ModelPort` adapter passes the same contract suite as its fake.
- [ ] `chassis serve` starts the chassis from config. The `chassis` package holds no business logic.
- [ ] `docker compose up` starts the chassis and the router with no manual steps.
- [ ] Streaming and complete responses carry the same output for the same input.
- [ ] Switching the model adapter (`fake` or `litellm`) or the model route needs a config change only.
- [ ] Token counts per request are visible in the router.
- [ ] Contract v0 is written down: the envelope, the events, `handle`, the mapping of chassis events to A2A, the ports, and a first draft of `EngineConnector`.

## How to run

```bash
make test-poc POC=01
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
