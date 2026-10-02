# Contract v2

The written contract after PoC-3: the public interface contract (native, OpenAI, Anthropic, MCP, `/manifest`, and the OpenAPI 3.1 spec), and what changed for workloads. Every change is additive over [contract v1](contract-v1.md), which stays as the PoC-2 record and still holds for everything this document does not name: the `handle` contract, the events, the A2A mapping, the lanes, the ports, the model proxy, and the MCP tool endpoint. Generated schemas: `packages/chassis/schemas/*.v0.json` (`make schemas`), now with `manifest.v0.json`. Source: `packages/chassis/src/chassis/core/inbound.py`, `core/manifest.py`, `server/interfaces/`, `server/manifest.py`, `adapters/openai_compat/`, `adapters/anthropic_compat/`, `adapters/mcp/agent.py`. Status: PoC-3, written 2026-10-01 from the code, updated the same day for the fix round (`spec.limits`, 413, `limit_exceeded`, `internal_error`, the fixed error text, strict native types, the MCP inner hop's headers). The decisions are in [ADR-003](../planning/adr/003-chat-formats-onto-the-canonical-request.md) (proposed). Every gap between the formats, with how it is handled and the test that pins it, is in `pocs/poc-03-one-interface-every-client/notes/2026-10-01-format-gaps.md`. Where a decision's text and the code differ, this document describes the code and says so at the end.

Contract v2 is a version of this document, not of the wire. The event schema is still `schema_version: "0"`.

## What did not change

- **The event schema.** `events.v0.json` is unchanged; `schema_version` stays `"0"`, and `SUPPORTED_SCHEMA_VERSIONS` is still `("0",)`.
- **`handle`.** Same signature, same wire form (dicts), same order rule. A workload may now receive two more keys inside `input.data` (see "What a workload may now receive"), but `TaskInput` and `request.v0.json` are unchanged: `data` was always an open object.
- **The ports.** No new port and no change to a port's promise. The interfaces sit in `server`, before the pipeline.
- **The A2A mapping.** `chassis.input`, `chassis.ctx`, `chassis.event`, and `chassis.schema_version` cross as in v1. `ctx` gets no interface field: the workload never learns which interface was used (011 H-2).
- **The lanes.** `inprocess`, `sidecar`, and `remote` (not built) are as in v1. The interfaces sit before the connector, so they cost the same in every lane: `inprocess` streams arrive in one batch, `sidecar` per delta, `remote` adds the network hop. MCP adds one in-process ASGI hop per call, in every lane.
- **The model proxy and the tool endpoint** on the proxy listener (`POST /v1/chat/completions` and `/mcp` on `127.0.0.1:8090`). The OpenAI message mapping moved to `chassis.adapters.openai_compat.messages`, shared with the OpenAI interface; the proxy's behavior did not change.

## The public interfaces

All on the public listener (`--port 8080`). Each HTTP interface is one OpenAPI operation, marked with the extension `x-chassis-interface: <name>` (suggested: the name), which the manifest reads.

| Interface | Method and path | `operationId` | Body | Answer | Streams |
| --------- | --------------- | ------------- | ---- | ------ | ------- |
| `native` | `POST /v1/run` | `run` | `RunRequest`: the `Request` with the ids and the agent optional | The `Response` envelope | Yes: one `event: <type>` frame per chassis event, then one `response` frame |
| `openai` | `POST /v1/chat/completions` | `chat_completions` (tag `openai`) | The `openai` SDK's `CompletionCreateParams` | `ChatCompletion`; streamed: `ChatCompletionChunk` frames and `data: [DONE]` | Yes |
| `anthropic` | `POST /v1/messages` | `messages` | The `anthropic` SDK's `MessageCreateParams` | `Message`; streamed: `message_start` ... `message_stop` | Yes |
| `mcp` | `/v1/mcp`, streamable HTTP, stateless | Not in the spec (`include_in_schema=False`) | One tool, named after the agent; its arguments are the `RunRequest` body | The native envelope as the tool result | No: complete only |

Other routes on the public listener: `GET /manifest` (`manifest`), `GET /health` (`health`), `GET /ready` (`ready`), `GET /openapi.json`. `/v1/models`, `/v1/responses`, and `/v1/messages/count_tokens` are 404.

- **`spec.limits`** bounds what one call may ask for, because the interfaces have no caller auth yet (PoC-8). suggested: every value. Each must be at least 1; refused at load otherwise.

  | Field | Default | Over it |
  | ----- | ------- | ------- |
  | `max_tokens_max` | 8000 | 400 `limit_exceeded` |
  | `timeout_ms_max` | 120000 | 400 `limit_exceeded` |
  | `messages_max` | 256 | 400 `limit_exceeded`: chat `messages`, or native `input.data.history` plus the input |
  | `body_bytes_max` | 1048576 (1 MiB) | 413, before the body is parsed |

  The three 400s are checked once in `serve`, after `to_request` (`server/interfaces/limits.py`, `enforce_limits`), so they cover every interface, MCP included. Refused, never clamped: a client that asked for more learns it. The chat adapters also count raw `messages` in `to_request`, with the same code.
- **`spec.interfaces`** switches the non-native interfaces: `{openai, anthropic, mcp}`, each a bool, each on by default (suggested). Unknown keys are refused at load. Native `/v1/run` is always on. An interface switched off is not mounted, not in the spec, and not in the manifest.
- **Two `/v1/chat/completions` routes.** The OpenAI interface (on the public port) is how a client calls the agent; `model` is the agent's name. The model proxy (on the proxy port) is how a workload calls a model; `model` is the LiteLLM route. Docs never write the bare path.
- **Two MCP routes.** `/v1/mcp` on the public port is the agent as a tool. `/mcp` on the proxy port is the workload's tools (`ToolPort`). The paths differ on purpose.

### The canonical request from a chat format

| Wire | Canonical |
| ---- | --------- |
| The last message; it must be `role: user` | `input.text`; text parts or blocks joined with `"\n"` |
| OpenAI `system` and `developer` messages; Anthropic `system`, then `role: system` messages; in order | `input.data.system`, joined with `"\n"`, only when not empty |
| Earlier `user` and `assistant` text turns | `input.data.history: [{"role", "text"}]`, only when not empty |
| `model` | Must equal `agent.name`, else 404. Not copied: `agent` and `agent_version` are the served ones |
| OpenAI `max_completion_tokens`, else `max_tokens` (both set and different is 400; missing is 2000); Anthropic `max_tokens`. Below 1 is 400 `invalid_body`; above `spec.limits.max_tokens_max` is 400 `limit_exceeded` | `budget.max_tokens`, the run's whole budget |
| `stream` | `request.stream` |
| No field | `budget.timeout_ms` is 30 000; `context_ref` is None |

Everything else is refused (400), ignored and counted, or dropped by the rule in ADR-003, item 1. The full list is in the gaps note.

### The answer from a run

- **OpenAI complete.** `ChatCompletion` with `id: chatcmpl-<request_id>`, `created` when the run opened, `model: <agent>`, one choice with the answer text and `finish_reason: stop`, and the run's whole `usage`.
- **OpenAI stream.** A role chunk, one chunk per `delta`, the rest of the answer as one more chunk when it extends the deltas, a finish chunk with `usage` (or, with `stream_options.include_usage`, a finish chunk and then a `choices: []` chunk with `usage`), then `data: [DONE]`. An error after text is one `data: {"error": ...}` frame, then `[DONE]`.
- **Anthropic complete.** `Message` with `id: msg_<request_id>`, `model: <agent>`, one text block (or `content: []` when there is no text), `stop_reason: end_turn`, `usage`.
- **Anthropic stream.** `message_start`, `content_block_start` at the first text, one `content_block_delta` per `delta` and for the rest, `content_block_stop` only when a block was opened, `message_delta` with `end_turn` and the run's usage, `message_stop`. No `ping`. An error after text is `event: error`, then the end.
- **The answer text** is `output.text` when it is a string, else the output as compact JSON with sorted keys (`chassis.core.inbound.answer_text`). Streamed text stands: when the deltas are not a prefix of the answer, nothing more is sent (`rest_of_answer`).
- **The hold rule.** An OpenAI or Anthropic stream sends nothing until the first `delta` or the terminal event. An `error` first is an HTTP error. The run is read in a task of its own (`server/interfaces/hold.py`), so the `chassis.run` span is entered and left in one context.
- **One call flow.** `server/interfaces/serve.py` (`serve`) runs every interface: not ready, `to_request`, open (with re-minting), count the ignored params, then complete or stream. `complete()` gives a `Reply`; `stream()` a `StreamReply`.

### Status table

For OpenAI and Anthropic; suggested: the whole table. Every row carries `x-should-retry`.

| Case | HTTP | OpenAI `type` / `code` | Anthropic `error.type` |
| ---- | ---- | ---------------------- | ---------------------- |
| Refused body or failed validation | 400 | `invalid_request_error` / `unsupported_parameter`, `unsupported_message`, or `invalid_body` | `invalid_request_error` |
| Over a `spec.limits` ceiling | 400 | `invalid_request_error` / `limit_exceeded` | `invalid_request_error` |
| Body over `body_bytes_max` | 413 | `invalid_request_error` / `limit_exceeded` | `invalid_request_error` |
| `model` is not the agent | 404 | `invalid_request_error` / `model_not_found` | `not_found_error` |
| Not ready | 503 | `server_error` / `not_ready` | `overloaded_error` |
| `a2a.timeout` | 504 | `server_error` / the code | `timeout_error` |
| `engine_error`, or a run with no `end` and no `error` | 500 | `server_error` / `engine_error` | `api_error` |
| `internal_error`: the chassis failed after the run opened | 500, `x-should-retry: false` | `server_error` / `internal_error` | `api_error` |
| Any other retryable code | 503 | `server_error` / the code | `overloaded_error` |
| Any other non-retryable code | 502 | `server_error` / the code | `api_error` |

- OpenAI body: `{"error": {"message", "type", "param", "code", "retryable"}}`. Anthropic body: `{"type": "error", "error": {"type", "message": "<code>: <message>"}, "request_id"}`.
- **The error text is fixed.** For a run error, OpenAI and Anthropic send the code and a fixed text per code (`core/inbound.py`, `PUBLIC_MESSAGES` and `public_message`; suggested: the texts), never the run's own message. An exception or a connector message can hold internal URLs or an upstream body; it stays in the log and on the `chassis.run` span. Native and MCP still carry the run's own message in `output.error.message`, as in contract v1.
- **`internal_error`.** When the chassis itself fails after the run opened (the adapter's `complete` or `stream` raising, a telemetry error), `serve` closes the run, logs the detail, and answers 500 `internal_error` with a fixed text on every interface: `x-should-retry: false` on OpenAI and Anthropic, so their SDKs do not run the agent again; `{"detail"}` on native.
- **A body FastAPI cannot validate or parse** is 400 in the route's format on OpenAI and Anthropic (`server/interfaces/errors.py`). A 4xx Starlette raises before the handler (a body that is not UTF-8, a 405) keeps its status and headers, in the route's body shape.
- **Native is strict and answers 422.** `RunRequest` is validated strictly: a value of the wrong JSON type (`true` or `"5"` for an integer, `0` for `stream`) is 422, not coerced. `budget.max_tokens` or `budget.timeout_ms` below 1 is also 422 on native (suggested: the minimum of 1). OpenAI and Anthropic answer a budget below 1 with 400 `invalid_body`.
- **413 on the four public paths.** A body over `spec.limits.body_bytes_max` is refused by a pure ASGI middleware (`limits.BodyLimit`) before it is parsed: at once when `Content-Length` is over the cap, else at the first byte over it. The body is the route's format: OpenAI's shape with code `limit_exceeded` on the OpenAI interface (`POST /v1/chat/completions`, public port), Anthropic's `invalid_request_error` on `POST /v1/messages`, `{"detail": str}` on `POST /v1/run` and on `/v1/mcp`. The MCP tool's in-process call to `/v1/run` passes through it too. 413 is declared on each route.
- Native and MCP answer a run's error as a 200 envelope with `status: error`. Native's own refusals before a run: 400 `{"detail"}` (another agent, or over a `spec.limits` ceiling), 409 `{"detail": {code, message}}` (`trace_id_in_use`), 413 `{"detail"}`, 422 (FastAPI's, for a body that fails validation), 503 `{"detail"}` (not ready).

### Headers

| Header | Direction | Where | Value |
| ------ | --------- | ----- | ----- |
| `traceparent` | In | Every interface | The run's trace id when valid and the native body sets none |
| `Idempotency-Key` | In | Every interface | The run's `idempotency_key`; wins over the native body's (suggested: the name) |
| `x-chassis-interface: mcp` | In | `/v1/run` | Set by the MCP tool's calls: `stream` is read as false, telemetry is labeled `mcp`, a body `trace_id` in use is re-minted. Grants nothing (suggested: the name) |
| `Authorization`, `x-api-key`, `anthropic-version`, `anthropic-beta` | In | OpenAI, Anthropic | Accepted and ignored; never logged or forwarded |
| `x-request-id`, `x-trace-id` | Out | OpenAI, Anthropic: every answer, errors and streams included | The run's ids (suggested: the names) |
| `request-id` | Out | Anthropic: every answer | The run's `request_id`, where the Anthropic SDK reads it |
| `x-chassis-status` | Out | OpenAI, Anthropic: complete 200 only | `ok`, `retry`, or `fallback`. Streams carry none: the headers go out before the status is known (suggested: the name) |
| `x-should-retry` | Out | OpenAI, Anthropic: every error | `true` or `false`, from the error's `retryable` |

## Changes to `/v1/run`

- **New input headers:** `traceparent` and `Idempotency-Key` when the body leaves those ids unset, and `x-chassis-interface: mcp` (see "Headers"). They are read raw, not declared as parameters, so the operation's inputs are the body alone and the MCP tool's arguments equal the body.
- **The id rule** (ADR-003, item 4). `trace_id`: the body, else a valid `traceparent`, else minted. `request_id`: the body, else minted; never a header. `idempotency_key`: the header, else the body, else minted. An empty string counts as unset.
- **Re-minting.** A trace id an in-flight run holds is re-minted once, counted as `chassis.trace_id_reminted{interface}` (suggested), when it came from a header or the call is MCP's. A native body `trace_id` keeps contract v1's 409. Contract v1's "one trace id, one in-flight run" still holds: re-minting keeps the key unique.
- **Described from the config.** The operation's `summary` and `description` name the agent and its version (`native.describe_run`). The MCP tool carries that description.
- **Declared answers.** 200 `application/json` (`Response`) and `text/event-stream`; 400, 409, 413, and 503 as small models.
- **Stricter validation.** `/v1/run` validates JSON types strictly at the HTTP boundary (`RunRequest`, `strict=True`): `true` for an integer, `"5"` for an integer, or `0` for a bool is 422, not coerced. `budget.max_tokens` and `budget.timeout_ms` below 1 are 422 (suggested: the minimum). `Budget`, `TaskInput`, and their published schemas are unchanged. The 200 `text/event-stream` is declared per event on every interface (`serve.SSE_EVENT_SCHEMA`): an object with a required `data` and optional `event`, `id`, and `retry`.
- **Ceilings.** A budget or a history over `spec.limits` is 400 `{"detail"}`; a body over `body_bytes_max` is 413 `{"detail"}` (see "Status table").

## What a workload may now receive

`input.data` may hold two keys the chassis sets from a chat format (suggested: the names):

| Key | Type | Set when |
| --- | ---- | -------- |
| `system` | string | The caller sent a system prompt. The agent's own prompt (`spec.prompt`) still rules; the workload may ignore it |
| `history` | `[{"role": "user" \| "assistant", "text": string}]` | The caller sent earlier turns |

Both are set only when not empty, so a one-turn chat call gives `data == {}`, as native `/v1/run` always did. A native caller may send the same keys itself; the chassis passes `data` through as is. The same logical request gives the same `input` from every interface.

## MCP

- **Generated, not written.** `build_agent_mcp` calls `FastMCP.from_openapi` over `run_operation_spec(app)`: the spec of the `/v1/run` operation alone, built by FastAPI's own `get_openapi`, so start-up never builds the whole spec (about 0.25 s with the two SDK request unions). A test holds it equal to that operation in the full spec. Route maps keep `POST /v1/run` as the one tool and exclude the rest. No `mcp_component_fn`, no hand-written tool.
- **The tool.** Named after the agent (`mcp_names`). Input schema: the `RunRequest` body's `properties` and `required`; FastMCP drops the body's top-level `title`, `description`, and `additionalProperties: false`. An unknown argument reaches `/v1/run` and is a 422, so an MCP tool error.
- **The call.** MCP client, `/v1/mcp`, the generated tool, `httpx2.ASGITransport(app)`, `POST /v1/run` with `x-chassis-interface: mcp`, the pipeline. No client timeout: the run's `budget.timeout_ms` bounds it. A request hook (`_run_headers`) runs last on every inner call: of the MCP request's headers it keeps only `traceparent`, `tracestate`, and `Idempotency-Key` (`FORWARDED_HEADERS`), plus the client's own transport headers, and it sets `x-chassis-interface: mcp` over any value a caller sent. `authorization`, `cookie`, and every other caller header are dropped.
- **Results.** A run with `status: error` is a normal tool result, not `isError`. Only a non-2xx from `/v1/run` is an MCP tool error.
- **Lifecycle.** The streamable HTTP app is built in the public app's lifespan; `/v1/mcp` answers 503 before and after it.

## `GET /manifest` (`schemas/manifest.v0.json`)

New, v0, suggested: the whole shape. Built per request; it needs no lifespan.

| Field | Value |
| ----- | ----- |
| `manifest_version` | `"0"` |
| `agent` | `{name, version}` from the config |
| `versions` | `{chassis, config?, prompt?, model_route?}`, as on every response |
| `lane` | `spec.engine.connector` |
| `event_schema_versions` | `SUPPORTED_SCHEMA_VERSIONS`, today `["0"]` |
| `interfaces` | Native first, then each OpenAPI operation that carries `x-chassis-interface` and is switched on in `spec.interfaces`, in spec order: `{name, method, path, operation_id, streaming, model}`. `streaming` means the operation declares a `text/event-stream` 200. `model` is the agent's name for a chat format and null for native. Then MCP, when mounted and on: `{name: "mcp", path: "/v1/mcp", transport: "streamable-http", streaming: false, tools: [...]}` |
| `openapi` | `{path, version, sha256}`; the sha256 is over the spec as JSON with sorted keys and no spaces |

Trust, image digests, scopes, events, governance, class, kind, and task arrive with 051 H-13.

## Telemetry

- `chassis.requests` and the `chassis.run` span carry `interface` (`native`, `openai`, `anthropic`, `mcp`).
- `chassis.inbound_ignored{interface, param}` counts each accepted-and-ignored param (suggested: the name).
- `chassis.trace_id_reminted{interface}` counts each re-mint (suggested: the name).

## Record and replay

Model calls in the interface suite replay from cassettes. `chassis_contracts.recording.CassetteTransport` wraps the `LiteLLMModel` transport only, never vcrpy's global patch, which would buffer the `sidecar` lane's SSE. One cassette per engine, `pocs/poc-03-one-interface-every-client/tests/cassettes/interfaces/<engine>.yaml`, holds exactly one model call for every interface, mode, and lane. Matching is on the body, so an interface that changed the canonical input fails replay. `make record-cassettes` re-records offline against the fake model server; the gate runs with `--record-mode=none`.

## Contract suites

| Suite | Checks | Bound to |
| ----- | ------ | -------- |
| `InboundAdapterContract` (new) | The same logical request gives the same canonical request; `to_request` is pure; the ids are the given ones; `model` must be the agent; refused bodies get the format's shape; ignored params change nothing; complete and stream map back and agree; usage is the sum of `metrics`; `end.output` without text is JSON text; `retry` and `fallback` are a normal answer; `tool_call` events are not client tool calls; an error before the first delta is HTTP; an error mid-stream is the format's frame; status and `x-should-retry` per code | native, openai, anthropic (`packages/chassis/tests/test_inbound_contract.py`) |
| `InterfaceContract` (new) | Every cell answers; stream and complete agree; every interface gives the same answer; model calls are charged to the run; the workload cannot tell the interface; one `traceparent` twice at once never conflicts; an error event is the format's error; MCP lists one tool named after the agent; the tool schema is the OpenAPI request schema; the spec is 3.1 and names every interface; the manifest matches the config and the spec | 4 interfaces x 4 engines x 2 modes x 2 lanes = 64 cells; 49 run, 15 skip with a reason (MCP x stream, 8; `echo-typescript` x `inprocess`, 8; one in both) (`pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py`) |
| Schemathesis | Every operation in the spec against its declared answers, deterministic, 25 examples (suggested); `positive_data_acceptance` off | `pocs/poc-03-one-interface-every-client/tests/test_openapi_props.py` |

The v1 suites are unchanged.

## Changes from v1 and why

All additive; none changes `events.v0.json`, `request.v0.json`, or `context.v0.json`, so `schema_version` stays `"0"`.

1. **The public interface contract:** OpenAI, Anthropic, and MCP interfaces over one call flow, `/manifest`, and the OpenAPI 3.1 spec with an `operationId` on every route. Why: the PoC-3 question, "can any client call any engine the same way" (011 H-2, 051 H-13).
2. **`/v1/run` reads `traceparent` and `Idempotency-Key`,** and `x-chassis-interface: mcp`. Why: 011 H-2 takes the trace and the idempotency key from headers for the chat formats, and one id rule for all four interfaces keeps them equal.
3. **`input.data.system` and `input.data.history`.** Why: chat callers send a system prompt and earlier turns; dropping them silently would change the answer, and refusing them would fail every multi-turn client. ADR-003, item 2.
4. **`spec.interfaces`.** Why: 051 H-13's `interface.mcp` flag, applied to every non-native interface.
5. **`schemas/manifest.v0.json`.** Why: the PoC-3 scope, "`GET /manifest`: a first version, built from the config and the OpenAPI spec".
6. **Re-minting instead of 409 on the non-native interfaces.** Why: SDK clients did not choose the trace id and retry a 409 by default. Contract v1's 409 stays for a native body `trace_id`. ADR-003, item 5.
7. **Stricter `/v1/run` validation.** Why: Schemathesis found `/v1/run` coerced `true` and `"5"` into integers and answered 200 for a body its own schema rejects. The three findings in `test_openapi_props.py` now pass.
8. **`spec.limits`, 413, and `limit_exceeded`.** Why: with no caller auth until PoC-8, any caller could ask for any budget, any turn count, and any body size. Refused, not clamped (suggested: the values).
9. **The fixed error text and `internal_error`.** Why: a run error's own message can hold internal URLs or an upstream body, and an exception inside the chassis after the run opened was a bare 500 an SDK would retry. Native and MCP keep contract v1's message (debt note).

### Where the code differs from the decisions' text

This document follows the code. These are the places where the open note, a planning doc, or the in-progress decisions say something else:

- **A minimum of 1 "on every interface":** the decision says refused; the status differs by interface. Native answers 422 (FastAPI, strict `RunRequest`), OpenAI and Anthropic 400 `invalid_body`.
- **A run with no `end` and no `error`:** OpenAI and Anthropic both answer 500 `engine_error` (complete) or an error frame (stream) (`InboundAdapterContract::test_a_run_with_no_end_and_no_error_is_an_error`). No lane produces such a run (the connector turns it into `a2a.transport`, the servers into `workload.no_end`), so only a test double reaches it.
- **The PoC-3 planning doc and 051 H-13 say `from_fastapi`:** the code uses `FastMCP.from_openapi` over the `/v1/run` operation's spec, because `from_fastapi` reads the whole spec at start-up. The exit criterion test accepts either.
- **The open note, section 9:** the matrix is 49 run and 15 skip, not 42 and 22; the binding is `test_interface_contract.py`, not `test_interfaces.py`. Corrected in the note.
- **The open note, section 2 (unknown keys):** "Unknown keys are dropped by the SDK type" holds for OpenAI only. The Anthropic router merges them back from the raw JSON, because anthropic 1.11's type lacks `temperature`, `top_p`, `top_k`, and `mcp_servers`.
- **The open note, section 3 (`x-chassis-status` with `ok`, `retry`, or `fallback`):** the header carries the response status as is. A run with `status: error` is an HTTP error on both chat formats, so a 200 never carries `error`.
- **011 H-2, "`error` events mapped to each format's error shape":** for OpenAI and Anthropic only. Native and MCP answer a run's error as a 200 envelope with `status: error`; MCP is a known gap.
- **051 H-13, "MCP adapter built with the MCP Python SDK":** built with FastMCP 4, as the PoC-3 scope says. Auth, scopes, and trust on MCP calls wait for PoC-8.
