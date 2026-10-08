# PoC-2: Two engines, one contract, two lanes: prove the chassis is framework-agnostic

Status: done (2026-10-01)
Planning doc: [002-PoC-2-two-engines-one-contract.md](../../docs/planning/poc/002-PoC-2-two-engines-one-contract.md)
Time box: 2 weeks

## Question

Can two very different frameworks run as workloads behind the same `handle` contract, over A2A in the `sidecar` lane and over A2A in memory in the chassis's tests, with no framework code in the chassis and no key in any workload? Does a workload that is not Python pass the same contract?

## What we built

In one line: the same chassis now runs four workloads in four styles behind one contract, either next to it over A2A on localhost or in memory for tests, and every model and tool call a workload makes comes back through the chassis.

```
                public 127.0.0.1:8080
 client ──POST /v1/run──▶ ┌───────────────────── chassis container ─────────────────────┐
                          │  /v1/run ─▶ engine connector (spec.engine.connector)        │
                          │               sidecar: A2A client, streams per event        │
                          │               inprocess: A2A in memory (tests, local only)  │
                          │                                                             │
                          │  proxy listener, 127.0.0.1:8090 only                        │
                          │    /v1/chat/completions ─▶ ModelPort ─▶ LiteLLM adapter ────┼──▶ LiteLLM router
                          │      keyed to its run by traceparent, budget per run        │    token_log per call
                          │    /mcp ─▶ ToolPort (glossary_lookup, defined once)         │         │
                          └──────────────┬─────────────────────────────▲────────────────┘         ▼
                    A2A over SSE         │                             │ model and MCP calls  fake model server
                    127.0.0.1:9000       │                             │ with traceparent,    (or a real model)
                                         ▼                             │ no key
                          ┌──────────── workload container, one at a time ──────────────┐
                          │  template A2A server ─▶ handle(input, ctx)                  │
                          │    workload_a2a: echo-python, echo-pydanticai,              │
                          │                  echo-langgraph                             │
                          │    @a2a-js/sdk:  echo-typescript                            │
                          └─────────────────────────────────────────────────────────────┘
                 shares the chassis's network namespace; holds no key, publishes no port
```

Events flow back up the same path: `start`, `delta`, `tool_call`, `metrics`, `end`. In the `sidecar` lane each event is one A2A update on the stream, so deltas arrive as they are made. In the `inprocess` lane they still arrive at the end of the run.

Three parts and one rule:

- **The chassis** now has two listeners. The public one (`/v1/run`, `/health`, `/ready`) is what callers reach. The proxy one binds localhost only and serves the model proxy and the MCP tool endpoint. Only a process in the chassis's network namespace, the sidecar, can reach it. The chassis still holds the only key.
- **The workloads** are four versions of the same simplifier: plain Python, PydanticAI, LangGraph, and TypeScript. Each turns its framework's output into chassis events in its own code, next to `handle`. The three Python ones are served by one shared package, `packages/workload-a2a`. The TypeScript one is a port of the same server to `@a2a-js/sdk`. None imports the chassis, and none holds a key.
- **The proxies** are how a workload reaches the outside. A model call goes to `/v1/chat/completions` on the proxy listener, which the chassis keys to its run by the `traceparent` header and charges to that run's budget. A tool call goes to `/mcp`, where `glossary_lookup` is defined once and served to every workload. LiteLLM still sits behind the model proxy and counts tokens.
- **The rule:** one wire contract. The same `handle` over the same A2A mapping gives the same events and the same envelope in every lane, and one contract suite checks it on every commit, offline.

Added in the fix round (2026-10-01):

- **Run deadline.** `budget.timeout_ms` is now a deadline for the whole run as well as the per-read timeout, in every lane; expiry is `a2a.timeout`, retryable (`LaneContract::test_the_run_deadline_ends_a_stalled_run_in_every_lane`).
- **409 on trace-id reuse.** A `/v1/run` whose trace id an in-flight run holds is refused with 409 `trace_id_in_use` (suggested) before the engine runs (`packages/chassis/tests/test_server.py::test_a_second_concurrent_run_with_an_in_flight_trace_id_is_409`).
- **Token reservation.** Each correlated model call reserves its `max_tokens`, capped at what the run has left, and settles on its usage, so concurrent calls of one run cannot share tokens (`packages/chassis/tests/test_model_proxy.py::test_a_concurrent_call_cannot_be_given_tokens_another_call_holds`).
- **Compose workload hardening.** Each workload container drops every capability, sets `no-new-privileges`, has a read-only root file system with a `tmpfs` on `/tmp` only, and runs as `10001:10001` (`tests/test_compose_sidecar.py::test_workload_runs_hardened[*]`, `::test_compose_user_matches_the_image_user[*]`). All four started and served under these settings in the Compose demo ([demo/2026-10-01-demo-sidecar.md](demo/2026-10-01-demo-sidecar.md)).

### What it is for

- **Pick a framework per team, not per platform.** A team writes `handle` in the framework it likes. The chassis does not change, and neither do auth, budgets, or tracing. PoC-6 uses exactly this to compare the shortlist on equal terms.
- **Any language.** The TypeScript echo gives the same events, number types included, as its Python twin. The contract has nothing Python in it.
- **Tools once.** A tool is defined once in the chassis and every framework gets it over MCP. No framework needs its own tool code.
- **Cost per run.** Because every model call comes back through the chassis with the run's trace id, the chassis can charge it to the right request and stop a run that is over budget, even with two runs in flight at once.

### What it is not yet

- No real model has served a sidecar workload. The Compose demo ran the `fake` variant only; `demo-sidecar.sh --local` needs a provider key in `deploy/compose/.env` and has not run, as in PoC-1. The planning doc's demo and exit criteria do not need a real model.
- The two framework workloads (PydanticAI, LangGraph) ran over the `sidecar` lane only in the Compose demo. In the gate they still run behind `inprocess`.
- The TypeScript echo has no tool client, so it does not run the `glossary_lookup` loop; the demo sends it a plain simplify prompt.
- In `inprocess`, the run deadline ends the stream but not `handle`, which runs on server-side until it finishes: no task id reaches the connector before the batch, so it cannot cancel.
- The MCP endpoint records the parsed trace id and the run's `request_id` but does not charge the run. A model call with no trace id is served and counted, with no per-replica budget.
- Token overhead is measured in request bytes, not tokens. Real counts need the `local` Compose variant.

The full list, each with its owner: [notes/2026-10-01-debt.md](notes/2026-10-01-debt.md).

## Scope

- [x] The `EngineConnector` interface: `setup`, `run` (an async stream of chassis events), `close`, and `capabilities`. Its `kind` is the lane. · evidence: `packages/chassis/src/chassis/ports/engine.py`; `tests/test_engines.py::TestFakeEngine::test_declares_lane_and_capabilities` and the same case in every binding; written in `docs/contracts/contract-v1.md` ("`EngineConnector`")
- [x] The `inprocess` connector from PoC-1, now refused outside the `fake` and `local` profiles. · evidence: `packages/chassis/tests/test_profiles.py::test_inprocess_refused_in_cloud`, `::test_inprocess_allowed_in_fake_and_local`, `::test_build_ports_refuses_inprocess_in_cloud`; `packages/chassis/tests/test_server.py::test_config_refuses_the_lane_the_profile_forbids`
- [x] The `sidecar` connector: an a2a-sdk client that sends the request to the workload on localhost and streams its events back. · evidence: `tests/test_lanes.py::test_sidecar_and_inprocess_give_the_same_stream[echo]`, `[echo_python]`, `[echo_python-error]` (Unix socket); `packages/chassis/tests/test_a2a_sidecar.py::test_deltas_arrive_one_at_a_time`, `::test_a_non_loopback_url_is_refused`; over loopback TCP: `packages/chassis/tests/test_a2a_sidecar.py::test_over_tcp_on_loopback` (`network`, `1 passed` today)
- [x] The template A2A server from PoC-1, now in its own container, serving A2A on localhost only. · evidence: the server is `packages/workload-a2a` (binds `127.0.0.1`, refuses another host; `uv run pytest packages/workload-a2a`); the container is checked offline by `tests/test_compose_sidecar.py::test_workload_shares_the_chassis_namespace_and_holds_no_key[workload-python]`, `::test_workload_healthcheck_reads_the_agent_card[workload-python]`, `::test_echo_python_image_is_pinned_and_locked`; live: `tests/test_compose_sidecar.py::test_demo_runs_every_workload_with_no_key` (`network`, passed 2026-10-01) and [demo/2026-10-01-demo-sidecar.md](demo/2026-10-01-demo-sidecar.md), where the chassis reads `echo-python 0.1.0` from the agent card on `127.0.0.1:9000` and the host gets "not reachable from the host: the workload publishes no port".
- [x] The mapping between chassis events and A2A task updates from PoC-1, revised with what the frameworks need. · evidence: `docs/contracts/contract-v1.md` ("Changes from v0 and why", items 1 and 4: chassis JSON as a string, `ctx.traceparent`); `packages/chassis/tests/test_a2a.py::test_chassis_json_crosses_as_a_compact_string`, `::test_a_v0_struct_event_is_still_read`; `EngineConnectorContract::test_json_values_survive_the_lane` in `packages/chassis/tests/test_contracts.py::TestInProcessConnectorEcho`, `::TestSidecarConnectorEcho`, `::TestSidecarConnectorWorkloadServer`
- [x] `spec.engine.connector` picks the lane: `inprocess` or `sidecar`. The framework is the workload's own choice, not a chassis setting. · evidence: `packages/chassis/tests/test_profiles.py::test_the_engine_is_spec_engine_connector_and_defaults_to_sidecar`, `::test_adapters_engine_is_refused_and_names_the_one_field`; `packages/chassis/tests/test_a2a_sidecar.py::test_registry_builds_the_sidecar_connector`; `tests/test_boundaries.py::test_no_framework_is_a_chassis_dependency`
- [x] A PydanticAI workload and a LangGraph workload, each in its own container with its own dependencies. · evidence: `packages/workloads/echo-pydanticai` and `packages/workloads/echo-langgraph`, each a `uv` workspace member with its own `pyproject.toml` and `Dockerfile`; `tests/test_engines.py::test_python_engine_passes_offline_against_the_fake_model_server[echo_pydanticai]`, `[echo_langgraph]`; offline Compose checks `tests/test_compose_sidecar.py::test_workload_shares_the_chassis_namespace_and_holds_no_key[workload-pydanticai]`, `[workload-langgraph]`, `::test_every_build_names_an_existing_dockerfile`; live: `tests/test_compose_sidecar.py::test_demo_runs_every_workload_with_no_key` (`network`, passed 2026-10-01) and [demo/2026-10-01-demo-sidecar.md](demo/2026-10-01-demo-sidecar.md): both images built, each container served `echo-pydanticai 0.1.0` and `echo-langgraph 0.1.0` over the `sidecar` lane, and each answered "From the glossary: SLM means small language model." with a `glossary_lookup` `tool_call` event. Caveat: in the gate both still run behind `inprocess`.
- [x] A TypeScript echo workload over the JavaScript a2a-sdk (suggested: `@a2a-js/sdk`), about 100 lines, in its own container. It proves that `TaskInput`, `Context`, and the event schema carry nothing Python-specific while the contract is still v0. · evidence: `packages/workloads/echo-typescript` on `@a2a-js/sdk` 1.3.0; `handle.ts` is 106 code lines (`notes/2026-10-01-measurements.md`, "Event mapping size"); the built workload (`node dist/src/main.js`) runs in the offline gate on a Unix socket, its model calls over another, in `tests/test_lanes.py::test_typescript_echo_gives_the_same_stream_as_echo_python` (same events, number types included, as `echo_python`); over loopback TCP in `::test_typescript_echo_gives_the_same_stream_as_echo_python_over_tcp` (`network`, `1 passed` today); its own `npm test` through `packages/workloads/echo-typescript/tests/test_node.py::test_npm_test_passes` (`1 passed` today); offline Compose checks `[workload-typescript]`; live: `tests/test_compose_sidecar.py::test_demo_runs_every_workload_with_no_key` (`network`, passed 2026-10-01) and [demo/2026-10-01-demo-sidecar.md](demo/2026-10-01-demo-sidecar.md): the image built on `node:24-slim` pinned by digest, the container served `echo-typescript 0.1.0`, and it answered "Plain words. Short sentences. Same facts." complete and streamed.
- [x] One event mapping per framework, in the workload: token deltas, tool calls, and usage metrics become chassis events. · evidence: `echo_pydanticai/mapping.py`, `echo_langgraph/mapping.py`, `echo_python/handle.py`, `echo-typescript/src/handle.ts` (sizes in `notes/2026-10-01-measurements.md`); deltas and metrics: `tests/test_engines.py::test_python_engine_passes_offline_against_the_fake_model_server[*]`; tool calls: `tests/test_tools.py::test_the_tool_works_on_each_engine[*]`
- [x] The same simplifier business logic written for each framework and for plain Python. · evidence: `tests/test_engines.py::test_python_engine_response_shape_streaming_and_complete[echo_python]`, `[echo_pydanticai]`, `[echo_langgraph]` (same text, prompt `simplifier-v1`); `tests/test_engines.py::test_typescript_echo_response_shape_streaming_and_complete` (gate, Unix sockets)
- [x] The chassis model proxy: an OpenAI-compatible endpoint on the chassis. It adds the scoped LiteLLM key and forwards the call. Each workload's model client points at it, and no workload holds a key. The proxy keys each call to its inbound request by `traceparent`, which each framework's HTTP client must propagate (suggested: OpenTelemetry httpx instrumentation in the workload). · evidence: `tests/test_run_correlation.py::test_a_forwarded_traceparent_charges_every_model_call_to_its_run[*]`, `tests/test_boundaries.py::test_python_engine_sends_no_key_and_calls_only_the_proxy[*]`, `packages/chassis/tests/test_model_proxy.py::test_inbound_authorization_is_never_forwarded`, `::test_a_call_of_an_in_flight_run_gets_a_span_and_is_charged`, `packages/chassis/tests/test_server.py::test_the_model_proxy_is_on_the_proxy_app_only`. Caveats: the key is still the PoC-1 key, not yet scoped (PoC-5, `pocs/poc-01-walking-skeleton/notes/2026-09-29-compose-key-debt.md`); no workload uses OpenTelemetry instrumentation, each sets `ctx.traceparent` as a plain header (contract v1, "Where the code differs").
- [x] An MCP tool endpoint on the chassis, backed by the fake `ToolPort`. One read-only tool (`glossary_lookup`) is defined once and served to every workload over MCP, so no framework needs its own tool conversion. · evidence: `tests/test_tools.py::test_the_tool_is_defined_once_in_the_chassis`, `::test_the_tool_is_served_by_the_chassis_over_mcp`, `::test_no_workload_defines_its_own_tool`, `::test_the_tool_works_on_each_engine[*]`; `packages/chassis/tests/test_server.py::test_the_tool_endpoint_is_on_the_proxy_app_only`
- [x] An import-lint rule: the chassis imports no framework package. · evidence: `tests/test_boundaries.py::test_import_lint_keeps_the_no_framework_rule`, `::test_the_rule_covers_the_chassis_and_the_frameworks`, `::test_the_rule_also_forbids_langchain`
- [x] The lane contract suite: every case runs over A2A on localhost and over A2A in memory, and must give the same envelope and event stream. · evidence: `packages/contract-suites/src/chassis_contracts/lane.py` (`LaneContract`), bound in `packages/chassis/tests/test_lane_contract.py::TestChassisLanes`; `tests/test_lanes.py::test_lane_contract_suite_passes_over_both_lanes`
- [x] Every engine is tested offline against the fake model server from PoC-1, including streaming and tool calls. · evidence: the three Python engines in the gate: `tests/test_engines.py::test_python_engine_passes_offline_against_the_fake_model_server[*]`, `tests/test_tools.py::test_the_tool_works_on_each_engine[*]`; the TypeScript echo against the fake model server over Unix sockets (no TCP, no key): `tests/test_lanes.py::test_typescript_echo_gives_the_same_stream_as_echo_python` (gate). The TypeScript echo calls no tool; the tool criterion names the three Python engines.
- [x] The `EnginePort` contract suite runs against the scripted fake engine and all three workloads. · evidence: `tests/test_engines.py::TestFakeEngine`, `::TestEchoPythonEngine`, `::TestEchoPydanticAIEngine`, `::TestEchoLangGraphEngine` (7 cases each, gate), and `::TestEchoTypeScriptEngine` (gate, over the `sidecar` connector on a Unix socket). The JSON-values and `traceparent` cases run for each Python engine's lane (its `json_values_engine` is the same `inprocess` connector on the suite's `JSON_VALUES_HANDLE`). Caveat: they skip, on purpose, in two bindings (4 of the 6 gate skips): `TestFakeEngine` (not a lane: no wire JSON and no `traceparent`) and `TestEchoTypeScriptEngine` (it serves one fixed `handle`; its integers and `traceparent` are checked in its own `npm test`).

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [x] All three Python engines pass their tests offline, against the fake model server. · evidence: `tests/test_engines.py::test_python_engine_passes_offline_against_the_fake_model_server[echo_python]`, `[echo_pydanticai]`, `[echo_langgraph]`; each workload's own tests in `packages/workloads/*/tests`
- [x] The TypeScript echo passes the lane contract suite with no chassis change. · evidence: `tests/test_lanes.py::test_typescript_echo_gives_the_same_stream_as_echo_python` (gate, Unix sockets), with the stock `SidecarConnector`; `::test_typescript_echo_gives_the_same_stream_as_echo_python_over_tcp` (`network`, `1 passed` today); `tests/test_engines.py::TestEchoTypeScriptEngine` (gate). Caveat: the TypeScript echo serves one fixed `handle`, so it cannot run the `LaneContract` cases that need a custom handle; the test compares its stream and envelope with `echo_python` under `workload_a2a`, both over Unix sockets, against one fake model server.
- [x] The lane contract suite passes: every case gives the same envelope and event stream over A2A on localhost and in memory. · evidence: `tests/test_lanes.py::test_lane_contract_suite_passes_over_both_lanes`, `::test_sidecar_and_inprocess_give_the_same_stream[*]`
- [x] The fake engine and all four workloads pass the same `EnginePort` contract suite. · evidence: `tests/test_engines.py::TestFakeEngine`, `::TestEchoPythonEngine`, `::TestEchoPydanticAIEngine`, `::TestEchoLangGraphEngine`, `::TestEchoTypeScriptEngine` (all gate). Caveat: the Python workloads are bound behind `inprocess`, and the JSON-values and `traceparent` cases skip for `TestFakeEngine` and `TestEchoTypeScriptEngine` (see the scope item above).
- [x] All four engines pass the same response-shape tests, streaming and complete. · evidence: `tests/test_engines.py::test_python_engine_response_shape_streaming_and_complete[echo_python]`, `[echo_pydanticai]`, `[echo_langgraph]`; `tests/test_engines.py::test_typescript_echo_response_shape_streaming_and_complete` (gate, Unix sockets)
- [x] Every outbound model and tool call carries the inbound request's trace id, per engine, and two concurrent requests in one replica each get their own budget. An engine whose client drops the header is listed, with the fix. · evidence: `tests/test_run_correlation.py::test_every_outbound_model_and_tool_call_carries_the_trace_id[echo_python]`, `[echo_pydanticai]`, `[echo_langgraph]`; `::test_a_forwarded_traceparent_charges_every_model_call_to_its_run[*]`; `::test_two_concurrent_requests_in_one_replica_each_get_their_own_budget`; the TypeScript echo's model call: `packages/workloads/echo-typescript/test/handle.test.ts` ("ctx.traceparent goes out on the model call as is"), run by `packages/workloads/echo-typescript/tests/test_node.py::test_npm_test_passes`. The list: no engine drops the header. Every engine sets `ctx.traceparent` as a plain header on its model and MCP clients, and that is the fix for any client that drops it. Caveat: the MCP endpoint records the parsed trace id and the run's `request_id` but does not charge the run (debt note).
- [x] The chassis has no import of any framework (lint passes). · evidence: `tests/test_boundaries.py::test_import_lint_keeps_the_no_framework_rule`; `make check` today: `Contracts: 7 kept, 0 broken`
- [x] No workload container holds a key. Each workload's model client points at the chassis model proxy. · evidence: `tests/test_boundaries.py::test_no_workload_service_holds_a_key`, `::test_no_workload_image_bakes_in_a_key`, `::test_no_workload_reads_a_key[*]`, `::test_python_workload_model_client_reads_the_proxy_url[*]`, `::test_python_engine_sends_no_key_and_calls_only_the_proxy[*]` (fake keys planted, none sent); `tests/test_compose_sidecar.py::test_workload_shares_the_chassis_namespace_and_holds_no_key[*]`, `::test_chassis_is_the_only_holder_of_the_key_and_the_port`. Live: `tests/test_compose_sidecar.py::test_demo_runs_every_workload_with_no_key` (`network`, passed 2026-10-01); [demo/2026-10-01-demo-sidecar.md](demo/2026-10-01-demo-sidecar.md) prints "key-like variables in the workload: none" for all four running containers. Caveats: the check allow-lists `GPG_KEY` by name (the python base image sets it to the public CPython release signing key id); `LITELLM_API_KEY` was empty in the fake variant, so the "no key in any log" check had no chassis key to look for.
- [x] The tool is defined once, served by the chassis over MCP, and works on all three engines. · evidence: `tests/test_tools.py::test_the_tool_is_defined_once_in_the_chassis`, `::test_the_tool_is_served_by_the_chassis_over_mcp`, `::test_no_workload_defines_its_own_tool`, `::test_the_tool_works_on_each_engine[echo_python]`, `[echo_pydanticai]`, `[echo_langgraph]`
- [x] Event mapping size (lines of code), token overhead against plain Python, the local hop's extra latency (p50 and p95), the overhead per streamed delta, and the time to first token are recorded. · evidence: `tests/test_records.py::test_measurements_are_recorded`; [notes/2026-10-01-measurements.md](notes/2026-10-01-measurements.md). Headline numbers (fake model, one laptop, Unix socket, not idle):
  - Mapping size, code lines per engine: PydanticAI 173, LangGraph 221, plain Python 237 (its tool loop included), TypeScript 316; the shared runtime 483, written once.
  - Token overhead, as request bytes against plain Python's 1008: PydanticAI +25 B (+2%), LangGraph -3 B (-0%). Caveat: bytes are the offline proxy for tokens (suggested: about 4 bytes per token); real counts need the `local` Compose variant.
  - The sidecar hop over `inprocess`: 1.96 ms at p50, 13.39 ms at p95 (2.11 ms of CPU at p50). Over calling `handle` directly: 6.52 ms at p50, 20.75 ms at p95.
  - Per streamed delta: 315.6 µs at p50 and 445.1 µs at p95 in `sidecar`, 221.6 µs and 335.1 µs in `inprocess`. Under the plan's suggested 0.5 ms per delta.
  - Time to first delta, `sidecar`: 10.53 ms at p50, 45.92 ms at p95. Per engine, `handle` called directly, over plain Python's 4.20 ms at p50: PydanticAI +1.34 ms, LangGraph +2.85 ms.
- [x] Contract v1 is written down: the `handle` contract, the chassis event schema, and its A2A mapping, with any changes from v0 and why. · evidence: `tests/test_records.py::test_contract_v1_is_written_down`; the document is [docs/contracts/contract-v1.md](../../docs/contracts/contract-v1.md)

Gate runs on 2026-10-01:

- `PATH=/opt/homebrew/bin:$PATH make test-poc POC=02` → `134 passed, 6 skipped in 19.62s`. The skips: 2 need TCP or Docker (the TCP twin and the Compose demo; sockets are disabled in the gate), and 4 are the JSON-values and `traceparent` suite cases `TestFakeEngine` and `TestEchoTypeScriptEngine` skip on purpose.
- With Docker up, `uv run pytest pocs packages -m network -p no:socket -q -rA` → `5 passed, 686 deselected in 113.18s`. The passes: `pocs/poc-02-two-engines-one-contract/tests/test_compose_sidecar.py::test_demo_runs_every_workload_with_no_key`, `tests/test_lanes.py::test_typescript_echo_gives_the_same_stream_as_echo_python_over_tcp`, `packages/chassis/tests/test_a2a_sidecar.py::test_over_tcp_on_loopback`, and PoC-1's `test_compose.py::test_compose_up_serves_one_request_and_counts_tokens` and `test_serve.py::test_chassis_serve_subprocess_answers_health`.
- `PATH=/opt/homebrew/bin:$PATH make check` → `673 passed, 18 skipped, 14 warnings in 34.74s`, mypy `Success: no issues found in 112 source files`, `Contracts: 7 kept, 0 broken.`, `planning-check OK`, `harness-lint: ok`.

## How to run

```bash
make test-poc POC=02                                                    # the offline gate
uv run pytest pocs/poc-02-two-engines-one-contract -m network -p no:socket -q   # TCP: the TypeScript twin, Compose
```

The TypeScript tests, in the gate and in the network run, need `npm ci` in `packages/workloads/echo-typescript` once; without `node_modules` they skip. The Compose sidecar stack, one workload at a time (from [deploy/compose/README.md](../../deploy/compose/README.md)):

```bash
cd deploy/compose
dc() { docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml "$@"; }
dc --profile pydanticai up -d --wait
curl -s -X POST 127.0.0.1:8080/v1/run -H 'content-type: application/json' \
  -d '{"input": {"text": "glossary: what does SLM mean?"}}'
dc --profile python --profile pydanticai --profile langgraph --profile typescript down
```

The measurements: `uv run python pocs/poc-02-two-engines-one-contract/demo/measure.py --runs 200 --warmup 10 --out notes/2026-10-01-measurements.md`.

## Demo

```bash
cd deploy/compose && ./demo-sidecar.sh
```

The script runs each of the four workload profiles next to the chassis in turn, sends one request complete and one streamed to each, prints the router's token lines and each workload's key-like environment, and writes its record to `demo/<date>-demo-sidecar.md`. It exited 0 on 2026-10-01 (fake variant). Output: [demo/2026-10-01-demo-sidecar.md](demo/2026-10-01-demo-sidecar.md). The record shows:

- Four engines swapped next to the chassis, each named by its agent card read inside the chassis: `echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-typescript`, all `0.1.0`. `/ready` answers `{"status":"ready"}` after each swap.
- The three Python engines get "glossary: what does SLM mean? Say it in plain words.", run the `glossary_lookup` tool through the chassis's MCP endpoint (a `tool_call` event with the chassis's definition in the stream), and answer "From the glossary: SLM means small language model.", complete and streamed.
- The TypeScript echo has no tool client, so it gets "simplify: the quick brown fox jumps over the lazy dog." and answers "Plain words. Short sentences. Same facts.", complete and streamed.
- The workload port `127.0.0.1:9000` is not reachable from the host, and no workload container has a key-like variable (`GPG_KEY` is allow-listed by name; see `deploy/compose/SECURITY.md`, section 6).
- LiteLLM logs 14 `token_log` lines tagged `agent:echo`: 4 per Python engine (two requests, two model calls each) and 2 for TypeScript.
- `LITELLM_API_KEY` is empty in the fake variant, so there is no chassis key to look for in the logs.

The offline check of the script is `tests/test_compose_sidecar.py::test_demo_script_swaps_all_four_and_writes_the_record`; the live run is `tests/test_compose_sidecar.py::test_demo_runs_every_workload_with_no_key` (`network`, passed 2026-10-01). Not run: `./demo-sidecar.sh --local` (real models; needs a provider key in `deploy/compose/.env`).

## Notes and decisions

- [notes/2026-10-01-open.md](notes/2026-10-01-open.md): decisions taken when PoC-2 opened: workload folder names, the shared `workload-a2a` package, the sidecar lane over a Unix socket in the gate, two listeners on the chassis (suggested: port 8090), the tool, and the pinned versions.
- [notes/2026-10-01-measurements.md](notes/2026-10-01-measurements.md): mapping size, token overhead, the hop's latency, per-delta overhead, and time to first token, with the command and the caveats.
- [notes/2026-10-01-debt.md](notes/2026-10-01-debt.md): the carried debt and open items, each with its owner.
- [notes/backlog-changes.md](notes/backlog-changes.md): what the backlog issues should change when this iteration closes.
- [docs/contracts/contract-v1.md](../../docs/contracts/contract-v1.md): contract v1, with the changes from v0 and where the code differs from the decided text.
- [ADR-002](../../docs/planning/adr/002-template-a2a-server-placement.md): where the template A2A server lives; amended 2026-10-01 for the shared `workload_a2a` package.
