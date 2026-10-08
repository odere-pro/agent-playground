# Contract v1

The written contract PoC-2's exit criterion asks for: the `handle` contract, the chassis event schema, and its A2A mapping, with the changes from v0 and why. It supersedes [contract v0](contract-v0.md), which stays as the PoC-1 record. Generated schemas: `packages/chassis/schemas/*.v0.json` (`make schemas`). Source models: `packages/chassis/src/chassis/core/`. Status: PoC-2, written 2026-10-01 from the code, after the four "Changes decided for v1" in contract v0 landed. Updated the same day after the PoC-2 fix round (run deadline, token reservation, 409 on trace-id reuse). Where a decision's text and the code differ, this document describes the code and says so under "Changes from v0 and why".

Contract v1 is a version of this document, not of the wire. The event schema is still `schema_version: "0"` (see "Events").

## The `handle` contract

```python
async def handle(input: TaskInput, ctx: Context) -> AsyncIterator[Event]: ...
```

Every service implements it in its workload. It yields chassis events. It is served over A2A in every lane: in memory in `inprocess`, on localhost in `sidecar`, over the network in `remote` (PoC-5).

`TaskInput`, `Context`, and `Event` name JSON shapes: `task_input.v0.json` (also `request.v0.json#input`), `context.v0.json`, and `events.v0.json`. A workload never installs the chassis package and there is no SDK ([ADR-001](../planning/adr/001-chassis-delivery-model.md), item 7), so the wire form is dicts:

```python
async def handle(input: dict, ctx: dict) -> AsyncIterator[dict]: ...
```

- **What `handle` gets.** `input` is exactly what the chassis sent (`chassis.input`), `{text, data}`. `ctx` is exactly `context.v0.json`, passed through unchanged by the template server. Integers stay integers both ways.
- **What `handle` yields.** Event dicts that match `events.v0.json`. The template server also takes objects with `model_dump(mode="json")` and dumps them. A missing `schema_version` is filled with `"0"`; in `workload_a2a`, `validate_event` fills it before it validates.
- **The order.** `start` first, once; `end` or `error` last; nothing after. The server enforces it (see "Error codes").
- **What `handle` forwards.** `ctx["traceparent"]`, when set, as the `traceparent` HTTP header, as is, on every model proxy call and every MCP request (`initialize`, `tools/list`, `tools/call`). Without it, no header. A child span id from an OpenTelemetry client is fine: the proxies key on the trace id only.
- **Where it calls.** Only the chassis's proxy listener, which binds localhost only:

| Variable | Default in the workloads | What |
| -------- | ------------------------ | ---- |
| `CHASSIS_MODEL_URL` | `http://127.0.0.1:8090/v1` (suggested: port 8090) | The model proxy, `POST {CHASSIS_MODEL_URL}/chat/completions`, OpenAI-compatible |
| `CHASSIS_TOOL_URL` | `http://127.0.0.1:8090/mcp` | The MCP tool endpoint, streamable HTTP, stateless, on the same listener |

- **No key.** A workload holds no key and sends no `Authorization` header of its own. An SDK that insists on a key string gets a placeholder (`not-a-key` in the PydanticAI and LangGraph workloads). The proxy never forwards an inbound `Authorization` header; the chassis's model adapter holds the credential (ADR-001, hard requirement 1).
- **No retry.** No workload retries a model call (the PydanticAI and LangGraph workloads set `max_retries=0`); retries and fallback are the chassis's.
- **Timeout.** The workloads use `ctx.budget.timeout_ms` as the timeout of each of their own model and tool calls (suggested: 30 s when unset). That is per call only: the connector owns the run deadline (see "Timeout and cancel"). `echo-pydanticai` sets no run deadline of its own on purpose.

Inside the chassis, the typed `Handle` alias and `echo` in `chassis.core.handle` stay: `FakeEngine` calls them directly, as a test double, not as a lane. `chassis.core.handle.wire(handle)` adapts a typed handle to the wire form (`echo_wire`), so the chassis's own tests can serve it over a lane.

## Events (`events.v0.json`)

A discriminated union on `type`. Every event carries `schema_version: "0"`. Unknown fields are refused. The chassis accepts the current and the previous major version (`SUPPORTED_SCHEMA_VERSIONS`, today `("0",)`).

| Event | Fields | Meaning |
| ----- | ------ | ------- |
| `start` | `request_id` | The first event, once. Its `request_id` must equal the request's |
| `delta` | `text` | A piece of the answer; deltas join to the full text |
| `tool_call` | `call_id`, `name`, `arguments {}`, `result?` | A tool the workload called through the chassis |
| `metrics` | `input_tokens`, `output_tokens`, `cost_usd?`, `model_route?`, `latency_ms?`, `attempt` | Usage per attempt; the collector sums tokens and cost |
| `end` | `status: ok | retry | fallback`, `output?` | The last event on success; `output` replaces the joined text when set |
| `error` | `code`, `message`, `retryable` | The last event on failure; the response gets `status: error` |

**The schema version stays `"0"`.** No event field changed in PoC-2: `events.v0.json` is byte for byte the PoC-1 file. The v1 changes are in how events cross A2A (a JSON string instead of a `Struct`) and one optional field in `Context`. Neither breaks a v0 reader of the schema, so there is no new major to add. The first breaking change to an event bumps `schema_version` to `"1"` and keeps `"0"` accepted.

**A failure before `handle` starts is one `error`, alone.** When the server refuses the request (`a2a.unsupported_schema_version`, `a2a.bad_request`), the first yielded event is not `start` (`workload.bad_order`), `handle` raises before `start` (`workload.exception`), or the connector fails before any event (`a2a.timeout`, `a2a.transport`), the stream is a single `error` with no `start`. The port's "`start` first" holds for every run that reaches `handle`'s first event.

## Envelope (`request.v0.json`, `response.v0.json`)

Unchanged from v0. From the epic's G.1, plus `versions` on every response.

- `Request`: `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version`, `input: TaskInput`, `context_ref?`, `stream`, `budget {max_tokens, timeout_ms}`.
- `Response`: the same identity fields, `output`, `metrics`, `status: ok | retry | fallback | error`, `versions {chassis, config?, prompt?, model_route?}`, `context_ref?`.
- `TaskInput`: `text?`, `data {}`. `context_ref` is accepted and passed through, unused until shared memory.
- `/v1/run` takes the `Request` with the ids and the agent optional; the chassis fills missing ids with a new UUID and refuses an `agent` or `agent_version` other than the one it serves (400).
- **One trace id, one in-flight run.** A `/v1/run` whose trace id another in-flight run holds is refused before the engine runs, streaming or not: 409 with `{"detail": {"code": "trace_id_in_use", "message": ...}}` (suggested: the status and the code), counted as `chassis.requests_refused` with `reason: trace_id_in_use`. The trace id is free again when that run ends. Why: the proxies key a call to its run by trace id ("The model proxy"), so two runs on one id could not be told apart. This is a PoC-2 stopgap: the trace id is not a credential, and PoC-5 replaces it with a chassis-minted per-run credential that the workload presents on every proxy call.

## Context (`context.v0.json`)

What the chassis gives a `handle` next to the input. It never holds a key.

| Field | Set by | Value |
| ----- | ------ | ----- |
| `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version` | The pipeline, from the request | As in the request |
| `budget` | The pipeline | `{max_tokens, timeout_ms}`, the request's |
| `versions` | The pipeline | `{chassis, config?, prompt?, model_route?}` |
| `model_route?` | The pipeline | `spec.model.route` |
| `traceparent?` (new in v1) | The connector, per run | `00-<trace id>-<parent id>-01`. The trace id is `trace_id_hex(ctx.trace_id)`: `ctx.trace_id` when it is 32 lowercase hex, else the first 32 hex of its sha256. The parent id is the connector's run span id when the telemetry adapter gives a W3C span id, else a fresh random 16 hex (suggested); so it is new on every run. `None` when a caller builds `ctx` without a connector |

`traceparent` is optional and additive, so a reader that does not know it treats it as absent. It is not a credential: a `remote` workload sees the trace id, and the proxies never treat a `traceparent` as proof of anything.

## Ports

| Port | Promise | Fake | Real adapter (arrives in) |
| ---- | ------- | ---- | ------------------------- |
| `ModelPort` | `complete` and `stream` agree; the last chunk carries `usage`; errors are `ModelError`; a two-turn tool conversation reaches the model with `call_id`, `arguments`, and `tool_call_id` intact | `ScriptedModel` (rules with `after_tool`, suggested) | `litellm`: `LiteLLMModel`, OpenAI-compatible HTTP over httpx, from `LITELLM_BASE_URL` and `LITELLM_API_KEY` (PoC-1); its client ignores proxy and certificate environment variables (`trust_env=False`), like the sidecar connector's; `vllm` (backlog 012 H-3) |
| `EngineConnector` | `kind` is a lane; `run` streams `start` first and `end` or `error` last; cancel is clean; JSON values and the run's `traceparent` survive the lane | `FakeEngine` (a test double, not a lane, not in the registry) | `inprocess` (PoC-1), `sidecar` (PoC-2), `remote` (PoC-5) |
| `ConfigPort` | `load` gives a version; a change gives a new version and notifies subscribers | `InMemoryConfig` | MinIO or S3 (PoC-4) |
| `TelemetryPort` | one span per call with attributes and parent; counters | `InMemoryTelemetry` | OpenTelemetry SDK (PoC-7) |
| `ToolPort` | `list_tools` is stable; each definition has a name, a description, and an object schema (`parameters`, the MCP input schema as is); a listed tool answers; errors are `ToolError` (`unknown_tool`, `bad_arguments`); a read-only tool repeats. Served to workloads over MCP at `/mcp` | `InMemoryTools` with `glossary_lookup` (`default_tools()`); `NoTools` is the `PortBundle` default | MCP gateway client, adapter name `mcp` (PoC-5, 054 H-16) |

`ToolPort` types: `ToolDefinition {name, description, parameters, read_only}`, `ToolResult {content, is_error}` (`is_error` is a failure the tool reports, not a transport failure), `ToolError {code, message, retryable}`. The fake checks arguments with a small hand-written JSON Schema check (`type`, `properties`, `required`, `additionalProperties: false`, `enum`), not `jsonschema`.

The other ports (`StatePort`, `EventPort`, `AuthPort`, `GuardrailPort`, `EvaluatorPort`, `FeedbackPort`, `RegistryPort`) arrive with their first adapter, in the same shape.

## `EngineConnector`

Final for the `inprocess` and `sidecar` lanes. `remote` (PoC-5) adds its keys.

```python
Lane = Literal["inprocess", "sidecar", "remote"]

class EngineConnector(Protocol):
    kind: Lane
    capabilities: frozenset[str]   # any of: streaming, tools, structured_output, code_exec, multi_agent
    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None: ...
    def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]: ...
    async def close(self) -> None: ...
```

Both lanes are `A2AConnector` (`chassis.adapters.a2a.connector`): the same client, mapping, validation, cancel, and span. Only the httpx transport and the URL differ. Both declare `capabilities = {"streaming"}`. `run` is one `chassis.engine.run` span per run (`lane`, `request_id`, and `a2a.task_id` once the server names the task). It never touches `ports.model`: the workload reaches models through the proxy.

`setup(config, ports)` gets the agent's `spec.engine` block as is (`EngineSpec.as_mapping()`, nulls dropped; unknown keys pass through):

| Key | Lane | Required | Value |
| --- | ---- | -------- | ----- |
| `connector` | all | no | The lane. Default `sidecar` (ADR-001 item 4) |
| `handle` | `inprocess` | yes | The workload's wire-form `handle`, as `module:attribute`, for example `echo_python:handle`. Loaded with `importlib`, the way an entry point is |
| `card` | `inprocess` | no | `{name, version, description}` for the in-process agent card. suggested: default `name` is the `handle` path, `version` is `CHASSIS_VERSION` |
| `url` | `sidecar` | yes | `http://127.0.0.1:<port>` or `http://localhost:<port>`: scheme `http`, a port, no path, no query, no fragment, no user info. Anything else is a `ValueError` naming ADR-001. suggested: port 9000 in `configs/sidecar.yaml` |
| `uds` | `sidecar` | no | A Unix socket path the requests go over instead of TCP, for tests and local runs. `url` still fills the `Host` header and must still be loopback |

There is no timeout key: `request.budget.timeout_ms` is both the per-read timeout and the run deadline (see "Timeout and cancel").

- **`inprocess`** builds the template server in the chassis process and calls it over `httpx.ASGITransport`. No socket. `httpx.ASGITransport` runs the whole app call before it returns the body, so the events arrive in one batch after `handle` returns: no per-delta streaming and no mid-run cancel. The run deadline still ends the stream (see "Timeout and cancel"), but it does not stop `handle`.
- **`sidecar`** reaches the workload's own A2A server on loopback, over TCP or `uds`. `setup` reads the agent card from the sidecar and fails at once with a `RuntimeError` naming the url when the sidecar is not up; it does not retry. A card whose interface URL is not loopback is refused, so the card cannot send the chassis's requests off the host. The httpx client ignores proxy environment variables (`trust_env=False`) for the same reason. The card comes from the sidecar (`workload-a2a serve --name --version --description`), so `card` is not read in this lane. Over a socket the events stream: each `delta` is yielded as it arrives, the timeout fires mid-run, and the cancel lands mid-run.

### The lane rule

- **`spec.engine.connector` is the one lane key.** `build_ports` builds the connector from `profiles.REGISTRY["engine"][spec.engine.connector]`: `inprocess` is `InProcessConnector`, `sidecar` is `SidecarConnector`, `remote` raises `AdapterNotAvailable` naming PoC-5.
- **`spec.adapters.engine` is refused** at load, with a message that names `spec.engine.connector`. `spec.adapters` names `model`, `config`, `telemetry`, and `tools`, and merges over the profile defaults per field: only the fields the agent sets change, and `adapters: {}` is the profile's defaults.
- **`inprocess` only in `fake` and `local`.** `check_lane` raises `LaneNotAllowed` elsewhere. It runs three times: when the config loads, in `build_ports`, and in the server's lifespan once the ports exist, for a built bundle and for an injected one. When the chassis built the bundle, the lifespan also checks `bundle.engine.kind == spec.engine.connector`. A mismatch fails startup.
- **`sidecar` is the default.** A config that wants `inprocess` names it (`configs/fake.yaml`, `configs/local.yaml`).
- **No fakes in `cloud`** (suggested). `build_ports` raises `AdapterNotAllowed` when `model`, `config`, `telemetry`, or `tools` resolves to `fake` or `memory` in `cloud`. `local` allows any mix. The `cloud` defaults (`s3`, `otel`, `mcp`) are not built yet, so today the `cloud` profile does not start; it fails with `AdapterNotAvailable` naming the PoC.
- **`FakeEngine` is not a lane.** A test passes it in a `PortBundle` it builds itself.

| Profile | Defaults (`model`, `config`, `telemetry`, `tools`) | Lanes allowed |
| ------- | -------------------------------------------------- | ------------- |
| `fake` | `fake`, `memory`, `memory`, `fake` | `inprocess`, `sidecar`, `remote` |
| `local` | `litellm`, `minio`, `otel`, `mcp` | `inprocess`, `sidecar`, `remote` |
| `cloud` | `litellm`, `s3`, `otel`, `mcp`; no `fake` or `memory` (suggested) | `sidecar`, `remote` |

## Chassis events over A2A

One `Request` is one A2A task. The chassis is the A2A client; the template A2A server wraps `handle`. The mapping is the same in every lane: only the URL and the httpx transport differ. It uses a2a-sdk 1.2 (A2A protocol 1.0, JSON-RPC binding at `/`, server-sent events for streaming) on the Python side and `@a2a-js/sdk` 1.3 on the TypeScript side. The 1.x types are protobuf classes in `a2a.types`; a `Part` has a `text`, `raw`, `url`, or `data` field.

**Chassis JSON crosses as a string.** A2A `metadata` and a data `Part` are protobuf `Struct`s, whose numbers are doubles. So the three chassis values travel as JSON strings, which cross every SDK byte for byte:

| Key | Where | Holds |
| --- | ----- | ----- |
| `chassis.event` | Every A2A stream event: the status update's `metadata`; for `delta`, the artifact's `metadata` | The event, with its `schema_version`, as a JSON string |
| `chassis.ctx` | `SendMessageRequest.metadata` | The `Context`, `traceparent` included, as a JSON string |
| `chassis.input` | `SendMessageRequest.metadata` | The whole `TaskInput`, `{text, data}`, as a JSON string; always set by the chassis |
| `chassis.schema_version` | `SendMessageRequest.metadata` | The event schema version the chassis speaks, a plain string, `"0"` |

- **Write:** Python `json.dumps(value, separators=(",", ":"), allow_nan=False)` (`mapping.dump_json`); TypeScript `JSON.stringify(value)`. A yielded event with `NaN` or `Infinity` is `workload.bad_event`.
- **Read:** `json.loads` or `JSON.parse` when the value is a string (`mapping.load_json`). An object value is the v0 `Struct` form: it is still read, as is, doubles and all, through v1, and dropped in v2. A value that is neither, or a string that does not parse to a JSON object, is `a2a.bad_event` on the connector and `a2a.bad_request` on the server.
- **Number limits, in both languages:** integers are exact up to 2^53 - 1; a workload sends a larger one as a string. TypeScript cannot tell `3` from `3.0`, so a reader of a float field accepts an integer.

### Request in

The connector sends one `SendMessageRequest` with `SendStreamingMessage`, always streaming; the collector folds the stream for `stream: false`.

| What | Where on the wire | The server reads it with |
| ---- | ----------------- | ------------------------ |
| `input` | `metadata["chassis.input"]`, a JSON string | `mapping.message_to_input`: `chassis.input` as is |
| `input.text` | The first `Part` of the `Message`, a text part, only when set. The native view for generic clients | Only when `chassis.input` is missing (a generic A2A client): `RequestContext.get_user_input()` |
| `input.data` | A data `Part` (`application/json`), only when not empty. The native view | Only when `chassis.input` is missing: the first data part, whose numbers are doubles |
| `ctx` | `metadata["chassis.ctx"]`, a JSON string | Passed to `handle` unchanged; missing is `{}` |
| Schema version | `metadata["chassis.schema_version"]` | Checked first; missing is `"0"` |
| Trace | The `traceparent` HTTP header on every A2A request of the run (the message and the cancel), equal to `ctx.traceparent`, in every lane, `inprocess` included. `Message.context_id` is `trace_id` | Not read by either server; they pass `ctx` through. The header is for OpenTelemetry on the server side and for generic tools |

`Message.role` is `ROLE_USER`. The card fetch in `setup` belongs to no run and carries no `traceparent`. Nothing else travels: no key, no other header of the chassis's own. The A2A protocol version is the `A2A-Version: 1.0` header, set by the client factory.

### Events out

Every chassis event is exactly one A2A stream event, in order, with the event under `chassis.event`. The connector reads only that key and validates it with `parse_event`. The parts are the A2A-native view of the same event, for generic clients such as an A2A inspector or another agent; the connector ignores them, and their numbers are doubles.

| Chassis event | a2a-sdk type | `TaskState` | Native parts | `TaskUpdater` call |
| ------------- | ------------ | ----------- | ------------ | ------------------ |
| `start` | `TaskStatusUpdateEvent` | `TASK_STATE_WORKING` | none | `update_status(WORKING, metadata=...)`. a2a-sdk needs a `Task` on the queue before the first status update, so the server enqueues one (`TASK_STATE_SUBMITTED`) first; the connector takes the task id from it and ignores it otherwise |
| `delta` | `TaskArtifactUpdateEvent` | unchanged | One text part with `text`, on the artifact `output`; `append` is false on the first delta and true after; `last_chunk` is never set | `add_artifact([new_text_part(text)], artifact_id="output", append=..., metadata=...)` |
| `tool_call` | `TaskStatusUpdateEvent` | `TASK_STATE_WORKING` | `status.message`: an agent message with one data part `{call_id, name, arguments, result}` | `update_status(WORKING, message=..., metadata=...)` |
| `metrics` | `TaskStatusUpdateEvent` | `TASK_STATE_WORKING` | none | `update_status(WORKING, metadata=...)` |
| `end` | `TaskStatusUpdateEvent` | `TASK_STATE_COMPLETED`, for `ok`, `retry`, and `fallback` alike; the chassis status is inside the event | `status.message`: an agent message with one data part `output`, only when set | `update_status(COMPLETED, message=..., metadata=...)` |
| `error` | `TaskStatusUpdateEvent` | `TASK_STATE_FAILED` | `status.message`: an agent message with one text part `message` | `update_status(FAILED, message=..., metadata=...)` |

A2A states the connector meets that are not chassis events (`mapping.update_to_event`):

| A2A event | Becomes |
| --------- | ------- |
| `TASK_STATE_CANCELED` | `error {code: "a2a.canceled"}`. The connector closes its stream before it sends its own cancel, so it never reads the `CANCELED` it caused; a `CANCELED` it reads came from someone else |
| Terminal update with no `chassis.event` | `COMPLETED` gives `end {status: ok}`; `FAILED` or `REJECTED` gives `error {code: "a2a.failed"}` |
| Non-terminal update with no `chassis.event` | Ignored |
| `TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_AUTH_REQUIRED` | `error {code: "a2a.unsupported_state"}`; not in v1 |

### Error codes

Every code below is an `error` event, the last event of the run. The server-side codes come with `TASK_STATE_FAILED` and `retryable: false`. The chassis template server, `workload_a2a`, and the TypeScript server emit the same codes in the same places.

| Code | Emitted by | When | `retryable` |
| ---- | ---------- | ---- | ----------- |
| `a2a.unsupported_schema_version` | Server | `chassis.schema_version` is not one the server accepts. Checked before anything else; `handle` does not run | false |
| `a2a.bad_request` | Server | `chassis.ctx` or `chassis.input` is not a JSON object (a string that does not parse to one, or a value of another type). After the version check, before `handle` runs. suggested: the code name | false |
| `workload.bad_event` | Server | A yielded value is not an event dict or model, fails the schema (the chassis copy with `parse_event`, `workload_a2a` with `jsonschema`, TypeScript with its own validator), names an unsupported `schema_version`, or holds `NaN` or `Infinity`. The message differs per server; the code does not | false |
| `workload.bad_order` | Server | An event before `start`, or a second `start` | false |
| `workload.no_end` | Server | `handle` returned without `end` or `error` | false |
| `workload.exception` | Server | `handle` raised | false |
| `a2a.bad_event` | Connector | `chassis.event` is not a JSON object, or fails `parse_event` (an unsupported `schema_version` included) | false |
| `a2a.request_mismatch` | Connector | `start.request_id` is not the request's | false |
| `a2a.timeout` | Connector | The run passed its deadline, `budget.timeout_ms` after the send, in every lane; or an HTTP read, connect, write, or pool wait took longer than `budget.timeout_ms` | true |
| `a2a.transport` | Connector | The connection failed or dropped (`httpx.TransportError`), or the a2a-sdk client failed (`A2AClientError`: an HTTP error status, a bad SSE frame, a network error). The sidecar died mid-stream, say. Also a stream that ends with no `end` or `error`; the task is then cancelled. suggested: the code name. The span gets `a2a.transport_error` with the exception type | true |
| `a2a.canceled`, `a2a.failed`, `a2a.unsupported_state` | Connector | See the table above | false |
| `engine_error` | Pipeline (`chassis.server.app`) | The connector raised instead of yielding an `error`, for example a JSON-RPC error answer, which a2a-sdk raises as an `A2AError` that is not an `A2AClientError` | false |

A workload's own failures use its own codes; they are a convention, not in the schema. All four workloads use `timeout`, `connect_error`, `http_<status>` (a budget refusal from the proxy is `http_429`), and `model_error` (in `echo-langgraph`, the catch-all for anything else). `echo-python`, `echo-pydanticai`, and `echo-typescript` add `bad_response`; `echo-python` adds `tool_error` and `tool_loop_exceeded` (suggested: at most 3 tool rounds). No workload uses `engine_error`, which is the pipeline's.

### Ids

| Chassis | A2A |
| ------- | --- |
| `request_id` | `chassis.ctx`'s `request_id` on the request, and `start.request_id` on the stream. The connector checks they match (`a2a.request_mismatch`) |
| `trace_id` | `Message.context_id`. One trace is one A2A context. Also the trace id of `ctx.traceparent` and of the header, as `trace_id_hex(trace_id)` |
| `Message.message_id` | A new UUID per send. A retry of the same `request_id` is a new message and a new task |
| `Task.id` | Assigned by the server. The connector records it as the span attribute `a2a.task_id` and uses it for `CancelTaskRequest` |
| `idempotency_key` | Inside `ctx` only. The pipeline handles idempotency before the connector runs |

### Versions

- The chassis accepts the current and the previous major `schema_version`, on `chassis.schema_version` and on every `chassis.event`. A bump adds the new major to `SUPPORTED_SCHEMA_VERSIONS` and keeps the old one. `workload_a2a` derives its accepted versions from the `schema_version` constants in its vendored `events.v0.json`.
- The chassis sends `chassis.schema_version: "0"`.
- The v0 `Struct` form of `chassis.event`, `chassis.ctx`, and `chassis.input` is read through v1 and dropped in v2.

### Timeout and cancel

- **`budget.timeout_ms` is two limits.** It is the a2a-sdk call timeout, which becomes `httpx.Timeout(timeout_ms / 1000)`: each connect, write, pool wait, and read of the SSE stream gets that long. It is also a deadline for the whole run, `timeout_ms` after the connector sends, checked on every read, in every lane, `inprocess` included. A run that keeps sending still ends at the deadline. Either limit ends the run with `error {code: "a2a.timeout", retryable: true}`.
- **The deadline in `inprocess` ends the stream, not `handle`.** `httpx.ASGITransport` delivers the events in one batch after `handle` returns, so at the deadline no event has arrived: the stream is a single `error`, with no `start`. No task id has reached the connector either, so it sends no cancel, and `handle` keeps running server-side until it finishes. A cancel path without a task id, or a streaming ASGI bridge, would close this gap (009 CH-1). In `sidecar` the task id arrives first, so the deadline cancels the task.
- **Cancel on any early end.** When the stream ends before the server sent its own `end` or `error`, and the connector knows the task id, it sends `CancelTaskRequest`, best effort. That covers a caller that closes the stream early (a client disconnect, `aclose()`), `a2a.timeout`, `a2a.transport` (a stream that ends with no `end` or `error` included), `a2a.bad_event`, and `a2a.request_mismatch`. The connector closes its stream first, then cancels, inside a shielded scope, so a caller's cancellation does not skip it. If closing the stream raises, the cancel is still sent and the failure is logged at `warning`. The cancel carries the run's `traceparent`. A cancel that does not apply (the task is already terminal) is logged at `debug`.
- **The cancel timeout** is its own: `CANCEL_TIMEOUT_S = 5.0` (suggested), because the run's budget may be spent already.
- **On the Python servers**, `cancel` never calls `aclose()`. It sets a flag and publishes `TASK_STATE_CANCELED`. `execute` checks the flag before and after each publish, stops at its next step, and closes the generator in its own task, because a generator may hold an anyio task group or cancel scope (PydanticAI, LangGraph) that must exit in the task that entered it. A pending await inside `handle` runs to its end. An update whose publish was already in flight when the cancel landed can still arrive after `CANCELED`. **On the TypeScript server**, `cancelTask` aborts the signal it passed to `handle` as `deps.signal`, which stops the model call, and publishes `TASK_STATE_CANCELED`.
- **The task store is pruned** on the Python servers (`PruningRequestHandler`): a task is deleted once it is terminal, and a cancelled task once the cancel is written. A task whose client left without a cancel and that finishes in the background is not pruned; the chassis connector always cancels a task it leaves. The TypeScript server uses a plain `InMemoryTaskStore`, not pruned.

### Cost per lane

| Lane | Transport | What each event costs |
| ---- | --------- | --------------------- |
| `inprocess` | `httpx.ASGITransport(app)`, no socket | Serialization only: one JSON encode and one decode per event on top of proto to JSON and back, with escaped quotes in the SSE frame. The events arrive after `handle` returns, in one body. Plus about 55 bytes of `traceparent` in `ctx` and in one header per request, per run, not per event |
| `sidecar` | `http://127.0.0.1:<port>`, or a Unix socket | The same plus one SSE frame over loopback per event. PoC-2 measures it per delta (the measurements note in `pocs/poc-02-two-engines-one-contract/notes/`) |
| `remote` (PoC-5) | The endpoint URL plus a credential interceptor | The same plus the network hop, TLS, and the per-remote credential. The proxies must also listen off loopback and authenticate each remote (ADR-001 item 8); not built |

The proxies cost the same in every lane that is built: the workload calls them on loopback. Model request bodies grow with the tool history.

## The model proxy

`POST /v1/chat/completions` on the proxy listener (`chassis.server.model_proxy`), OpenAI-compatible, mapped onto `ModelPort`. Streaming and complete.

- **In.** The OpenAI body. The proxy takes `model` (the route), `messages`, `temperature` (suggested: `0.0` when unset; backlog 012 H-3 decides), `max_tokens`, `tools` (function tools: `name`, `description`, `parameters`), and `stream`. Other body keys, `stream_options` included, are accepted and ignored: the last streamed chunk always carries `usage`.
- **Messages.** Each OpenAI message maps to one `ModelMessage`, tool loop included:

```python
class ModelMessage(BaseModel):  # frozen, extra="forbid"
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_calls: list[ToolCallRequest] | None = None  # assistant only; [] is read as None
    tool_call_id: str | None = None                  # tool only, and required there
```

| OpenAI message field | Port |
| -------------------- | ---- |
| `content` string | As is |
| `content` list of text parts only | Joined in order with `"\n"` (suggested) |
| `content` list with any other part (`image_url`, `input_audio`, `file`) | 400 |
| `content` null or missing | `None`; allowed only on an assistant message with `tool_calls` |
| Assistant `tool_calls[i]`: `{id, type: "function", function: {name, arguments}}` | `ToolCallRequest(call_id=id, name=name, arguments=json.loads(arguments))`; `arguments` must be a string that parses to an object, else 400 |
| Tool message | `tool_call_id` and `content` required |
| `name` | Carried, on any role |
| Another key whose value is null or empty (`refusal`, `audio`, `function_call`, `annotations`, as SDKs echo them back) | Ignored |
| Another key with a value, or another role (`developer`, `function`) | 400 |

- **The refusal:** 400 with `{"error": {"code": "unsupported_message", "type": "invalid_request_error", "param": "messages[<i>].<field>", "message": ...}}`, before the model is called and before the call is counted. Never a 200 on a changed message.
- **Out.** The OpenAI shape. Complete: one `chat.completion` with `content` (null when empty), `tool_calls` with `arguments` as a JSON string, `finish_reason` `tool_calls` or `stop`, and `usage`. Stream: a first chunk with `role`, a chunk per text piece and per tool call (with its `index`), a last chunk with `finish_reason` and `usage`, then `data: [DONE]`. A `ModelError` on `complete` is 502 when retryable, else 500, with `{"error": {"message", "type": "model_error", "code", "retryable"}}`; mid-stream it is one error frame with the same body, then `[DONE]`.
- **Correlation.** The proxy reads the `traceparent` header (`parse_traceparent`, in `chassis.core.trace` and re-exported from `chassis.server.correlation`: any version but `ff`, a non-zero trace id and parent id, exactly 55 characters for version `00`; it keys on the trace id only and never raises). It looks the trace id up in `app.state.runs`, which holds one `RunRecord` per in-flight `/v1/run`. A match runs the call in the span `chassis.model.call` (`route`, `trace_id`, `request_id`, `agent`, `correlated: true`) and charges its usage to that run: the `complete` usage, or the last streamed `usage` even when the stream fails or the client leaves. Two in-flight runs never share a trace id (`/v1/run` answers 409 `trace_id_in_use`, see "Envelope"), so each call names at most one run.
- **Per-run budget.** A run's remaining tokens are its `budget.max_tokens` less what it has spent (input plus output) and what its in-flight calls hold. For a correlated call, the forwarded `max_tokens` is capped at the remainder, and is the remainder when the workload sets none. The call reserves that amount at its start and settles it on its usage (or releases it when the model was never called), so two concurrent calls of one run are never given the same tokens.
- **Refused when nothing is left.** A call whose run has no remaining tokens is refused before the model is called: 429 with `{"error": {"type": "budget_exhausted", "code": "budget_exhausted", "retryable": ..., "message": ...}}`; for `stream: true`, a 429 whose body is that error as one SSE frame, then `[DONE]`. `retryable` is `true` only when the tokens are not spent but held by in-flight calls, which may settle for less; `false` when they are spent. `max_tokens` bounds output only, so a run can still overshoot its budget by a prompt's input tokens, or by a model that ignores `max_tokens`. Two concurrent runs never share a record, so each keeps its own budget.
- **Uncorrelated calls are served and counted.** A call with no `traceparent`, a malformed one, or one that names no in-flight run is served with its own `max_tokens`, counted as `chassis.model_calls_uncorrelated` per route, and logged at `warning` with the reason. It is not charged to any run and has no budget of its own. Refusing it is PoC-5 egress work.
- **Counters.** `chassis.model_calls` per route for every accepted call; `chassis.model_calls_uncorrelated` and `chassis.model_calls_refused` per route.
- **Before the lifespan** the proxy answers 503.

## The MCP tool endpoint

`/mcp` on the proxy listener (`chassis.server.tool_endpoint`, `chassis.adapters.mcp`), MCP over streamable HTTP, stateless (`stateless_http=True`), FastMCP 4.

- **The tool list equals `ToolPort.list_tools()`.** The MCP server is built once, in the chassis's lifespan, from `state.ports.tools`: one MCP tool per definition, with the definition's own `name`, `description`, and `parameters` as the input schema (not a schema re-derived from a Python signature), and `read_only` as `read_only_hint`. The fake profile serves `glossary_lookup`, defined once in `chassis.fakes.tool`. No workload defines a tool of its own.
- **A call** goes to `ToolPort.call(name, arguments)`. A `ToolResult` becomes an MCP result: `content` as text (a string as is, else its JSON), `structured_content` when it is an object, and `isError` from `is_error`. A `ToolError` (`unknown_tool`, `bad_arguments`) becomes an MCP tool error result, `isError: true`, whose text and structured content are `{code, message, retryable}`; never a transport failure or a 500.
- **Telemetry.** One `chassis.tool.call` span per call, with `tool`; `trace_id`, the trace id parsed from the inbound MCP HTTP request's `traceparent` with `parse_traceparent` (nothing when the header is absent or invalid, so a raw header never reaches telemetry); and `request_id` when an in-flight run holds that trace id. `chassis.tool_calls` per tool. The endpoint looks the run up to name it, but does not charge it, and does not count uncorrelated tool calls.
- **Stateless** means each MCP request stands alone. `echo-python` opens a short session per operation, so no MCP task group stays open while `handle` yields.
- **Before and after the lifespan** `/mcp` answers 503.
- Tool descriptions and outputs are data, never instructions (054 H-16).

## The two listeners

`chassis serve` runs two uvicorn servers in one event loop. When either stops, so does the other.

| Listener | Flags (default) | Routes |
| -------- | --------------- | ------ |
| Public | `--host 127.0.0.1 --port 8080` | `/health`, `/ready`, `/v1/run` |
| Proxy | `--proxy-host 127.0.0.1 --proxy-port 8090` (suggested) | `/v1/chat/completions`, `/mcp` |

The proxy app shares the public app's `state` (ports, `ready`, `runs`, config) and has no lifespan of its own. A `--proxy-host` that is not loopback is refused unless `--allow-any-proxy-host` is given (the `remote` lane, with proxy authentication, which is not built). `--proxy-port` must differ from `--port`. The public port never carries the proxies (ADR-001, hard requirement 1).

## Where the template A2A server lives

Decided in [ADR-002](../planning/adr/002-template-a2a-server-placement.md), option A. PoC-2 ships the workload-side copy as one shared workspace package, not as an `a2a_server.py` in each workload; that is the form ADR-002 item 4 takes, and it keeps ADR-002's rules (no chassis import, a copied mapping, `jsonschema` validation).

| Where | Module | Holds | Validates with | Imports from `chassis` |
| ----- | ------ | ----- | -------------- | ---------------------- |
| Chassis | `chassis.adapters.a2a.mapping` | The mapping above, as pure functions over dicts: `request_to_message`, `message_to_input`, `event_to_update`, `update_to_event`, `dump_json`, `load_json` | Nothing; validation is the caller's | Nothing. a2a-sdk only |
| Chassis | `chassis.adapters.a2a.server` | `HandleExecutor`, `PruningRequestHandler`, `build_agent_card`, `build_app` | `parse_event` | `chassis.core.events` |
| Chassis | `chassis.adapters.a2a.{connector,inprocess,sidecar}` | `A2AConnector`, `InProcessConnector`, `SidecarConnector` | `parse_event` | Anything it needs |
| Python workloads | `packages/workload-a2a` (`workload_a2a.mapping`, `.server`, `.cli`, `schemas/events.v0.json`) | The same server, plus `build_server`, `serve`, and `workload-a2a serve --handle module:attribute --port N` (or `--uds PATH`). Binds `127.0.0.1` and refuses a host that is not loopback unless `--allow-any-host` | `jsonschema` against the vendored `events.v0.json`, with the schema's defaults filled in, so the wire JSON equals the chassis copy's | Nothing, ever (import-linter) |
| TypeScript | `packages/workloads/echo-typescript/src/a2a_server.ts`, `schema.ts`, `schemas/events.v0.json` | A port of the same mapping and executor to `@a2a-js/sdk` 1.3, with the Express app | Its own validator against the vendored `events.v0.json` | Not applicable |

- **`mapping.py` is byte-equal** in the chassis and in `workload_a2a`, and the two `events.v0.json` files are equal: `packages/workload-a2a/tests/test_workload_a2a_copies.py` fails when they differ. Change the chassis copy first, then copy the file.
- **`server.py` is mirrored,** not copied. The difference is the validation, the loopback bind, and `build_server` and `serve`.
- **The TypeScript server is a port,** kept in step by a twin test against `echo_python` (`pocs/poc-02-two-engines-one-contract/tests/test_lanes.py`, offline over Unix sockets; a TCP variant is `network`) and by `packages/chassis/tests/fixtures/events_from_typescript.jsonl`, which the chassis validates.
- The three Python workloads (`echo-python`, `echo-pydanticai`, `echo-langgraph`) are served by `workload-a2a serve` in the `sidecar` lane; `echo-python` also runs in `inprocess` (`configs/fake.yaml`). A workload is a `uv` workspace member so the chassis's tests can import it; the dependency runs one way.

## Contract suites

| Suite | Checks | Bound to |
| ----- | ------ | -------- |
| `EngineConnectorContract` | Lane and capabilities declared; `start` first and `end` or `error` last; events survive the wire; `metrics` keep their integers; cancel is clean; JSON values survive the lane (`{"n": 3, "big": 2^53 - 1, "f": 1.5, "nested": [1, {"k": 2}]}` in `tool_call.arguments`, `end.output`, and `input.data`, and `ctx.budget.max_tokens`, as `int`); the run's `traceparent` reaches `handle` (version `00`, the run's trace id, a non-zero parent id) | `FakeEngine`, `inprocess`, `sidecar`, and the workloads |
| `LaneContract` | The same `handle` gives the same events, in order, and the same `Response` over every lane, compared pairwise; a handle that yields no `schema_version` gives the same stream in every lane; a bad event is `workload.bad_event` in every lane; a retryable `error` and an `end.output` cross intact; a handle that stalls past `budget.timeout_ms` ends in one retryable `a2a.timeout` in every lane, well before it would wake (its own test, since `inprocess` gets no event before the deadline); the `traceparent` case per lane. Two values are masked: the `traceparent` parent id and the `workload.bad_event` message | `inprocess`, `sidecar` (the chassis's server over a Unix socket), `sidecar-workload-server` (`workload_a2a`). The TypeScript echo serves one fixed `handle`, so it is compared with its Python twin instead (`test_lanes.py`) |
| `ModelPortContract` | `complete` and `stream` agree; usage; a tool call round trip; `ModelError`; a two-turn tool conversation reaches the model intact (`received_messages`) | `ScriptedModel`, `LiteLLMModel` against the fake model server |
| `ToolPortContract` | A stable, well-formed list; a known call answers; `unknown_tool`; `bad_arguments`; a read-only tool repeats | `InMemoryTools` |

## Changes from v0 and why

Four changes, all decided in the PoC-1 review ("Changes decided for v1 (2026-10-01)" in contract v0), and five more that PoC-2 added. None changes `events.v0.json`, and the one schema change adds an optional field to `context.v0.json`, so `schema_version` stays `"0"`.

1. **Chassis JSON crosses A2A as a string** (`chassis.event`, `chassis.ctx`, and the new `chassis.input`). Why: A2A `metadata` and data parts are protobuf `Struct`s, whose numbers are doubles, so a workload's `3` in `input.data`, `tool_call.arguments`, or `end.output` came back as `3.0`; only `metrics` survived, by pydantic coercion. A JSON string crosses every SDK byte for byte, in Python and TypeScript. The v0 `Struct` form is still read through v1. Cause: PoC-1 review, decision 1.
2. **The lane is named once: `spec.engine.connector`.** `spec.adapters.engine` is refused, `FakeEngine` left the registry, `spec.adapters` merges per field, the lifespan checks the lane again once the ports exist, `sidecar` is the default, and `cloud` refuses in-memory adapters (suggested). Why: with two fields, `profile: cloud`, `adapters.engine: inprocess`, and `engine.connector: sidecar` passed the guard and ran in process, and `adapters: {}` in `cloud` built every fake. Two fields that must agree are the bug; ADR-001 item 4 names one. Cause: PoC-1 review, decision 2.
3. **`ModelMessage` carries the tool loop,** and the proxy refuses what it cannot carry with a 400. Why: PoC-2's PydanticAI and LangGraph workloads send the model's `tool_calls` and the tool results back through the proxy; v0 dropped `tool_calls` and `tool_call_id`, turned `content: null` into the string `"null"`, and answered 200. `ScriptedModel` and the fake model server gained `after_tool` rules (suggested) so a scripted tool loop ends. Cause: PoC-1 review, decision 3.
4. **The run's `traceparent` travels in `ctx`,** and the `traceparent` header is set in every lane, `inprocess` included. Why: the workload must forward it on its model and MCP calls, or the proxies cannot charge a call to its run (ADR-001 item 6); the Python template server's `RequestContext` does not give `handle` the HTTP headers, and the TypeScript port had passed the header as a third `deps` argument. A field in `ctx` reaches `handle` the same way in every language and lane. Cause: PoC-1 review, decision 4.
5. **`EngineConnector` is final, with the `sidecar` lane and its keys `url` and `uds`.** Why: PoC-2's question is two lanes, one contract; the gate refuses TCP, so the `sidecar` lane is tested over a Unix socket (`uds`), and `url` stays loopback only because the sidecar serves localhost only (ADR-001). A transport failure is now `error {code: "a2a.transport", retryable: true}` (suggested), because a sidecar that dies mid-stream must end the run with an event, not an exception. `budget.timeout_ms` became a run deadline as well as the per-read timeout, because a stream that keeps sending never hit the per-read one. Cause: the PoC-2 scope and the PoC-2 open note, "The `sidecar` lane in the offline gate".
6. **The proxies moved to a localhost-only listener, and the MCP tool endpoint joined them.** Why: in PoC-1 the model proxy sat on the public port, which spends with the chassis key; ADR-001 hard requirement 1 keeps localhost for the proxies only. Cause: PoC-1 debt, `pocs/poc-01-walking-skeleton/notes/2026-09-29-inprocess-debt.md`, and the PoC-2 open note, "Two listeners on the chassis".
7. **The model proxy keys each call to its run and enforces the run's budget.** Each correlated call reserves its capped `max_tokens` and settles on its usage; a second in-flight run on the same trace id is refused with 409 (suggested). Why: the PoC-2 exit criterion "two concurrent requests in one replica each get their own budget", and ADR-001 item 6; without the reservation, concurrent calls of one run could each be given the whole remainder, and without the 409, two runs on one id could not be told apart. Uncorrelated calls are served and counted, not refused, because PoC-2 must list the engines whose client drops the header; refusing them is PoC-5 egress work.
8. **`ToolPort` is a port with a fake, served over MCP at `/mcp`.** Why: the tool must be defined once and served to every engine, so no framework needs its own tool conversion (PoC-2 scope). v0 listed `ToolPort` twice, in the table and among the ports still to come; it is in the table only now.
9. **The workload-side template server is `packages/workload-a2a`,** one shared package, instead of an `a2a_server.py` per workload, and the Python servers prune the task store. Why: three Python workloads would otherwise hold three copies to keep equal; one package keeps ADR-002's rules with one copy and one byte-equality test. Pruning pays the PoC-1 debt of a task store that kept every task. Cause: the PoC-2 open note, "The template A2A server for Python workloads", confirmed here.

### Where the code differs from the decisions' text

This document follows the code. These are the places where the decided text, or a planning doc, says something else:

- **Timeout (v0, "Versions and cancel"; decision text):** v0 reads as if `budget.timeout_ms` is the timeout per run. The code applies it both per HTTP read, connect, and write and as a run deadline, in every lane. In `inprocess` the deadline ends the stream but not `handle`, which runs on server-side until it finishes: no task id reaches the connector before the batch, so it cannot cancel (009 CH-1).
- **`CANCELED` after the connector cancelled (v0 table, suggested: "nothing, the stream ends cleanly"):** the connector closes its stream before it cancels, so it never reads that `CANCELED`; `update_to_event`'s `cancelled` flag is never set by the connector. The row is unreachable on the chassis path.
- **Decision 4, the second suite case** ("the workload calls the model proxy and `chassis.model_calls_uncorrelated` stays 0, over every lane and against both servers"): the check exists, in `packages/chassis/tests/test_server.py`, `test_model_proxy.py`, and `pocs/poc-02-two-engines-one-contract/tests/test_run_correlation.py`, not as a case of the lane suite. The "equals the A2A request's header" check is in `packages/chassis/tests/test_a2a_trace.py`, per lane, not in the suite either.
- **Decision 1, "a v0 `Struct` `chassis.event` is still read":** covered by `packages/chassis/tests/test_a2a.py`, not by a contract suite case.
- **Decision 4, the TypeScript `deps`:** `deps` keeps `signal`, as decided, and also `fetch` and `modelUrl`, which callers and tests set; the server passes only `signal`. None of it is on the wire.
- **ADR-001 item 6, "a call with no trace id counts against a per-replica budget":** there is no per-replica budget. An uncorrelated call is served, counted, and logged, with no limit.
- **ADR-001 item 6, "the proxies key every outbound call":** the model proxy does; the MCP endpoint records the parsed trace id and, for an in-flight run, its `request_id` on its span, but does not charge the run or count uncorrelated tool calls.
- **ADR-001 item 6, the key itself:** the proxies key on the trace id, which is not a credential. `/v1/run` refuses a second in-flight run on one trace id (409, suggested) so the key stays unique. PoC-5 replaces it with a chassis-minted per-run credential.
- **ADR-001 item 6 and the PoC-2 plan, "suggested: OpenTelemetry httpx instrumentation":** no workload uses it. Each sets `ctx.traceparent` as a plain header on its model and MCP clients.
- **ADR-002 item 4, "`a2a_server.py` in the workload container":** it is the `workload_a2a` package (change 9). ADR-002 was amended on 2026-10-01 to say so ("Amendment (2026-10-01)").
- **The TypeScript vendored `events.v0.json`** equals the chassis's today, but no test checks it; only `mapping.py` and the `workload_a2a` schema copy have a diff test.
- **`a2a.unsupported_state`'s message** still says "not in contract v0".
- **A JSON-RPC error answer from a server** is not mapped by the connector; it becomes the pipeline's `engine_error`, not an `a2a.*` code.
