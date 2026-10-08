# PoC-1: Walking skeleton: testable from day 0, one request through the chassis and the router

Status: done
Planning doc: [001-PoC-1-walking-skeleton.md](../../docs/planning/poc/001-PoC-1-walking-skeleton.md)
Time box: 1 week

## Question

Is the chassis testable offline from day 0, with every dependency behind a swappable port? Does one request go end to end through the chassis and the router, streaming and complete, with tokens counted?

## What we built

In one line: a generic HTTP front door that runs any agent logic behind it over one contract, with every model call counted and every key hidden.

```
 client ──POST /v1/run──▶ ┌──────────────── chassis (one process) ────────────────┐
                          │  /v1/run ─▶ inprocess connector ─▶ A2A (in memory)     │
                          │                                        │              │
                          │                     template A2A server ─▶ handle()    │
                          │                                   (echo_python, no key)│
                          │                                        │              │
                          │  /v1/chat/completions ◀────────────────┘              │
                          │        │  model proxy                                 │
                          │        ▼                                              │
                          │  ModelPort: ScriptedModel (fake) │ LiteLLMModel (key) │
                          └────────────────────────────────────┬──────────────────┘
                                                               │ agent:echo tag
                                                               ▼
                                              LiteLLM router ──▶ fake model server
                                              big-default │ local-small   (or a real model)
                                              token_log per call
```

Events flow back up the same path: `start`, `delta`, `metrics`, `end`. `/v1/run` returns them either as one JSON response or as a stream of server-sent events.

Three parts and one rule:

- **The chassis** is a plain web server. It knows nothing about simplifying text. It takes a request, hands it to whatever is plugged in behind it, and returns the answer. It is the only thing that holds a key.
- **The workload** is the agent logic, today a 100-line simplifier in `packages/workloads/echo-python`. It talks to the chassis through one fixed contract: dicts in, events out, over A2A. It could be rewritten in another language and the chassis would not change.
- **The router** (LiteLLM) sits between the chassis and the models. Every model call passes through it, so tokens and cost are counted in one place, and switching from a cloud model to a local one is a config line.
- **The rule:** every outside dependency sits behind a port with a fake. The whole thing runs and tests offline, with no keys, in under a second.

### What it is for

Today it does one small thing: it simplifies a sentence. The point is the shape, not the task. Once this shape holds:

- **A standard way to ship an agent.** A team writes `handle()`, picks a config, and gets auth, limits, budgets, tracing, and evals for free, because those go into the chassis once, not into every agent. PoC-9 turns this into `agentctl new`.
- **Framework freedom.** PydanticAI, LangGraph, an OpenAI Agents SDK loop, or a TypeScript agent all plug in behind the same contract. PoC-2 and PoC-6 use exactly this to compare them on equal terms and pick the ones to support.
- **Cheap models without a rewrite.** Because every model call goes through the router, moving a task from a big API model to a small local one is a route name change. That is the epic's SLM goal.
- **Untrusted code, contained.** The same chassis can front code you do not trust, in a sandboxed pod, with no key and no network. It does not need to be different software. PoC-5 proves that.

### What it is not yet

- Streaming in the in-memory lane is batched: the events arrive after `handle` returns. Paid in PoC-2 for the `sidecar` lane, which streams each event over a socket; `inprocess` still batches. See [PoC-2](../poc-02-two-engines-one-contract/README.md).
- The model proxy sat on the chassis's public port. Paid in PoC-2: the proxies are on a localhost-only listener, `127.0.0.1:8090` (suggested). See [PoC-2](../poc-02-two-engines-one-contract/README.md).
- The `inprocess` connector set no `traceparent`. Paid in PoC-2: every lane sets the header and `ctx.traceparent`, and the model proxy charges each call to its run. See [PoC-2](../poc-02-two-engines-one-contract/README.md).
- The router keys are the master key, and the agent tag can be faked by a caller. PoC-5 gives each service its own scoped key.
- The real-model variant of the Compose stack has never been run. It needs a provider key.

Each of these is written down in [notes/](notes/).

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

- [x] The `chassis` package and the `chassis serve` launcher. The same package later becomes the one generic chassis image, with no business logic in it. · evidence: `tests/test_serve.py::test_chassis_serve_starts_from_config`, `tests/test_serve.py::test_chassis_package_holds_no_business_logic`; the console script itself is covered by `tests/test_serve.py::test_chassis_serve_subprocess_answers_health` (`network`, not run today)
- [x] Chassis core: the envelope, the event model (`start`, `delta`, `tool_call`, `metrics`, `end`, `error`), and `async def handle(input, ctx) -> AsyncIterator[Event]`.
- [x] A collector that turns the event stream into one complete response.
- [x] `POST /v1/run` with `stream: true` (server-sent events) and `stream: false`. · evidence: `tests/test_serve.py::test_stream_and_complete_carry_the_same_output`, `tests/test_a2a_inprocess.py::test_plugin_answers_over_a2a_in_memory`
- [x] `GET /health` and `GET /ready`. · evidence: `tests/test_serve.py::test_chassis_serve_starts_from_config` (`/ready` is 503 before lifespan, 200 after); live: `demo/2026-09-29-demo-fake-variant.md`
- [x] The template A2A server: a small a2a-sdk server that wraps `handle`, and one written mapping between chassis events and A2A task updates. It is the one wire contract from the first commit. · evidence: `tests/test_a2a_inprocess.py::test_a2a_messages_validate_against_the_sdk_types`; the mapping is in `docs/contracts/contract-v0.md` ("Chassis events over A2A"); placement: [ADR-002](../../docs/planning/adr/002-template-a2a-server-placement.md)
- [x] The `inprocess` connector: the chassis loads that server in its own process and calls it over A2A in memory (an ASGI transport, no socket). ADR-001 allows it for the chassis's own tests and local runs. The `sidecar` lane, with the same server in its own container, comes in PoC-2. · evidence: `tests/test_a2a_inprocess.py::test_plugin_answers_over_a2a_in_memory` (asserts `InProcessConnector` is the engine and every `chassis.engine.run` span carries `a2a.task_id`)
- [x] A plain-Python business logic plug-in: a simple simplifier prompt, served by the template A2A server. · evidence: `tests/test_a2a_inprocess.py::test_plugin_answers_over_a2a_in_memory`; the plug-in is `packages/workloads/echo-python` (`spec.engine.handle: echo_python:handle`)
- [x] The first real adapter: `ModelPort` over OpenAI-compatible HTTP, pointed at LiteLLM. LiteLLM runs in Docker Compose, pinned by hash, with two named routes: `big-default` (an API model) and `local-small` (a small model on llama.cpp or vLLM). · evidence: `tests/test_model_adapter.py::test_real_adapter_streams_and_completes_the_same_text`, `tests/test_compose.py::test_every_image_is_pinned_by_digest`, `tests/test_compose.py::test_local_config_switches_model_adapter_to_litellm` (both routes named); both routes answer live in `demo/2026-09-29-demo-fake-variant.md`. Caveat: only the `fake` variant ran today, where both routes point at the fake model server; the `local` override (API model and llama.cpp) is not verified.
- [x] Token and cost counts per request in LiteLLM, tagged with the agent name. · evidence: `tests/test_compose.py::test_compose_up_serves_one_request_and_counts_tokens` (`network`, 1 passed today); four `token_log ... total_tokens=51 ... tags=agent:echo` lines in `demo/2026-09-29-demo-fake-variant.md`. Caveat: `cost_usd=0.000000` for the fake model; a real cost needs the `local` override, not verified today.
- [x] The response reports the `versions` used (config, prompt, model route). · evidence: `tests/test_serve.py::test_stream_and_complete_carry_the_same_output`; live: `versions {chassis 0.1.0, config <hash>, prompt simplifier-v1, model_route ...}` in `demo/2026-09-29-demo-fake-variant.md`

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [x] `make test` passes offline with the `fake` profile: no network, no keys. · evidence: `pocs/poc-01-walking-skeleton/tests/test_day0.py`, `packages/chassis/tests/test_contracts.py`
- [x] Each of the four ports has an interface, a fake, and a contract suite that the fake passes. · evidence: `pocs/poc-01-walking-skeleton/tests/test_day0.py`, `packages/chassis/tests/test_contracts.py`
- [x] The plain-Python plug-in answers over A2A in memory, and the A2A messages validate against the a2a-sdk types. · evidence: `tests/test_a2a_inprocess.py::test_plugin_answers_over_a2a_in_memory`, `tests/test_a2a_inprocess.py::test_a2a_messages_validate_against_the_sdk_types`
- [x] The real `ModelPort` adapter passes the same contract suite as its fake. · evidence: `tests/test_model_adapter.py::test_real_adapter_binds_the_same_contract_suite_as_the_fake`, `packages/chassis/tests/test_contracts.py::TestLiteLLMModel` (the binding, run by `make check`: 170 passed, 2 skipped)
- [x] `chassis serve` starts the chassis from config. The `chassis` package holds no business logic. · evidence: `tests/test_serve.py::test_chassis_serve_starts_from_config`, `tests/test_serve.py::test_chassis_package_holds_no_business_logic`; the console script: `tests/test_serve.py::test_chassis_serve_subprocess_answers_health` (`network`, skipped in the gate, not run today)
- [x] `docker compose up` starts the chassis and the router with no manual steps. · evidence: offline `tests/test_compose.py::test_compose_files_exist`, `tests/test_compose.py::test_chassis_waits_for_a_healthy_router`; live (`network`, needs Docker): `uv run pytest pocs/poc-01-walking-skeleton/tests/test_compose.py -k compose_up -p no:socket` → `1 passed`; output in `demo/2026-09-29-demo-fake-variant.md`
- [x] Streaming and complete responses carry the same output for the same input. · evidence: `tests/test_serve.py::test_stream_and_complete_carry_the_same_output`, `tests/test_a2a_inprocess.py::test_plugin_answers_over_a2a_in_memory`; live on both routes in `demo/2026-09-29-demo-fake-variant.md`
- [x] Switching the model adapter (`fake` or `litellm`) or the model route needs a config change only. · evidence: `tests/test_model_adapter.py::test_switching_the_model_adapter_is_a_config_change`, `tests/test_model_adapter.py::test_litellm_without_a_base_url_is_a_clear_error`, `tests/test_compose.py::test_local_config_switches_model_adapter_to_litellm`; the route switch `big-default` → `local-small` is a config override and a restart in `demo/2026-09-29-demo-fake-variant.md`
- [x] Token counts per request are visible in the router. · evidence: `tests/test_compose.py::test_compose_up_serves_one_request_and_counts_tokens` (`network`, `1 passed` today); the `token_log` lines in `demo/2026-09-29-demo-fake-variant.md`. They come from a custom callback, not `/spend/logs`; see `notes/2026-09-29-compose-key-debt.md`
- [x] Contract v0 is written down: the envelope, the events, `handle`, the mapping of chassis events to A2A, the ports, and a first draft of `EngineConnector`. · evidence: `tests/test_a2a_inprocess.py::test_contract_v0_is_written_down`; the document is `docs/contracts/contract-v0.md`

Gate runs on 2026-09-29: `make test-poc POC=01` → `32 passed, 2 skipped` (the two skips are the `network` tests: the console-script launcher and the live Compose run). `make check` → `170 passed, 2 skipped`, mypy `Success: no issues found in 63 source files`, `lint-imports` 5 kept 0 broken.

Gate runs on 2026-10-01, after PoC-2's fix round: `PATH=/opt/homebrew/bin:$PATH make test-poc POC=01` → `34 passed, 2 skipped, 6 warnings in 0.84s` (the same two `network` skips: the console-script launcher and the live Compose run). `PATH=/opt/homebrew/bin:$PATH make check` → `673 passed, 18 skipped, 14 warnings in 33.22s`, mypy `Success: no issues found in 112 source files`, `Contracts: 7 kept, 0 broken.`, `planning-check OK`, `harness-lint: ok`.

## How to run

```bash
make test-poc POC=01
```

What to install, how to run one chassis process or the Compose stack, and the `curl` calls: [HOW-TO-RUN.md](HOW-TO-RUN.md).

## Demo

```bash
cd deploy/compose && ./demo.sh
```

The script brings up the `fake` variant of the Compose stack (chassis, LiteLLM, fake model server), checks `/health` and `/ready`, sends one request complete and one streamed on `big-default`, switches `model.route` to `local-small` with a config override, repeats both, then prints the router's token log and stops the stack. Output: [demo/2026-09-29-demo-fake-variant.md](demo/2026-09-29-demo-fake-variant.md). Both routes return the same text with `input_tokens 42, output_tokens 9`, and LiteLLM logs four `token_log` lines tagged `agent:echo`.

## Notes and decisions

- [notes/2026-09-29-compose-key-debt.md](notes/2026-09-29-compose-key-debt.md): the master key doubles as the chassis key in the `local` variant (deadline PoC-5); the `fake` variant runs with no master key; token counts come from a custom callback because there is no Postgres; the agent tag is spoofable until virtual keys.
- [notes/2026-09-29-inprocess-debt.md](notes/2026-09-29-inprocess-debt.md): in the `inprocess` lane the events arrive after `handle` returns (no mid-run timeout or cancel, no `traceparent`); the a2a-sdk task store is never pruned; the model proxy sits on the public port. All PoC-2.
- [ADR-002](../../docs/planning/adr/002-template-a2a-server-placement.md): the template A2A server lives under `chassis.adapters.a2a`; the workload sees `handle` as dicts in and dicts out and never depends on `chassis`.
- [notes/backlog-changes.md](notes/backlog-changes.md): what the backlog issues should change when this iteration closes.
