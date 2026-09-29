# Contract v0

The written contract PoC-1's exit criterion asks for. Generated schemas: `packages/chassis/schemas/*.v0.json` (`make schemas`). Source models: `packages/chassis/src/chassis/core/`. Status: PoC-1 walking skeleton. The A2A mapping below is the wire contract from the first commit; it becomes part of v1 in PoC-2 when the `sidecar` lane runs the same server in its own container.

## The `handle` contract

```python
async def handle(input: TaskInput, ctx: Context) -> AsyncIterator[Event]: ...
```

Every service implements it in its workload. It yields chassis events. It is served over A2A in every lane: in memory in `inprocess`, on localhost in `sidecar`, over the network in `remote`.

`TaskInput`, `Context`, and `Event` name JSON shapes: `request.v0.json#input`, `context.v0.json`, and `events.v0.json`. Inside the chassis they are pydantic models. A workload sees them as plain dicts, because it never installs the chassis package and there is no SDK ([ADR-001](../planning/adr/001-chassis-delivery-model.md), item 7). So the wire form is:

```python
async def handle(input: dict, ctx: dict) -> AsyncIterator[dict]: ...
```

The template A2A server validates every yielded dict against the event schema before it goes on the wire. A workload may yield its own pydantic models instead; the server calls `model_dump(mode="json")` on anything that has it. Inside the chassis, the typed `Handle` alias and `echo` in `chassis.core.handle` stay as they are: `FakeEngine` calls them directly, as a test double, not as a lane. `chassis.core.handle.wire(handle)` adapts a typed handle to the wire form (`echo_wire`), so the chassis's own tests can serve it over a lane.

## Events (`events.v0.json`)

A discriminated union on `type`. Every event carries `schema_version: "0"`. Unknown fields are refused. The chassis accepts the current and the previous major version.

| Event | Fields | Meaning |
| ----- | ------ | ------- |
| `start` | `request_id` | The first event, once |
| `delta` | `text` | A piece of the answer; deltas join to the full text |
| `tool_call` | `call_id`, `name`, `arguments`, `result?` | A tool the workload called through the chassis |
| `metrics` | `input_tokens`, `output_tokens`, `cost_usd?`, `model_route?`, `latency_ms?`, `attempt` | Usage per attempt; the collector sums tokens and cost |
| `end` | `status: ok | retry | fallback`, `output?` | The last event on success; `output` replaces the joined text when set |
| `error` | `code`, `message`, `retryable` | The last event on failure; the response gets `status: error` |

## Envelope (`request.v0.json`, `response.v0.json`)

From the epic's G.1, plus `versions` on every response.

- `Request`: `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version`, `input: TaskInput`, `context_ref?`, `stream`, `budget {max_tokens, timeout_ms}`.
- `Response`: the same identity fields, `output`, `metrics`, `status: ok | retry | fallback | error`, `versions {chassis, config?, prompt?, model_route?}`, `context_ref?`.
- `TaskInput`: `text?`, `data {}`. `context_ref` is accepted and passed through, unused until shared memory.

## Context (`context.v0.json`)

What the chassis gives a `handle` next to the input: the identity fields, `budget`, `versions`, `model_route?`. It never holds a key.

## Ports (day 0)

| Port | Promise | Fake | Real adapter (arrives in) |
| ---- | ------- | ---- | ------------------------- |
| `ModelPort` | `complete` and `stream` agree; the last chunk carries `usage`; errors are `ModelError` | `ScriptedModel` | `litellm`: `chassis.adapters.litellm.LiteLLMModel`, OpenAI-compatible HTTP over httpx, built from `LITELLM_BASE_URL` and `LITELLM_API_KEY`; passes the same suite offline against the fake model server (PoC-1) |
| `EngineConnector` | `kind` is a lane; `run` streams `start` first and `end` or `error` last; cancel is clean | `FakeEngine` (a test double, not a lane) | `inprocess` A2A in memory (PoC-1), `sidecar` (PoC-2), `remote` (PoC-5) |
| `ConfigPort` | `load` gives a version; a change gives a new version and notifies subscribers | `InMemoryConfig` | MinIO or S3 (PoC-4) |
| `TelemetryPort` | one span per call with attributes and parent; counters | `InMemoryTelemetry` | OpenTelemetry SDK (PoC-7) |

The other ports (`StatePort`, `EventPort`, `ToolPort`, `AuthPort`, `GuardrailPort`, `EvaluatorPort`, `FeedbackPort`, `RegistryPort`) arrive with their first adapter, in the same shape.

## `EngineConnector`, draft

```python
class EngineConnector(Protocol):
    kind: Literal["inprocess", "sidecar", "remote"]
    capabilities: frozenset[str]   # streaming, tools, structured_output, code_exec, multi_agent
    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None: ...
    def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]: ...
    async def close(self) -> None: ...
```

`inprocess` is allowed only in the `fake` and `local` profiles (`chassis.profiles.check_lane`).

`setup(config, ports)` gets the agent's `spec.engine` block as `config`. For `inprocess`:

| Key | Required | Value |
| --- | -------- | ----- |
| `connector` | yes | `inprocess` |
| `handle` | yes | The workload's `handle`, as `module:attribute`, for example `echo_python:handle`. Loaded with `importlib`, the way an entry point is |
| `card` | no | `{name, version, description}` for the agent card. suggested: default `name` is the `handle` path, `version` is `versions.chassis` |

There is no timeout key: `request.budget.timeout_ms` is the timeout per run. `sidecar` (PoC-2) and `remote` (PoC-5) replace `handle` with `url`; `remote` adds the per-remote credential reference. The connector uses `ports.telemetry` for one span per run; it does not touch `ports.model`, because the workload reaches models through the chassis's proxy, not through the connector.

In memory, the events arrive after `handle` returns: `httpx.ASGITransport` runs the whole app call before it returns the body, so the `inprocess` lane has no per-delta streaming, `budget.timeout_ms` never fires mid-run, and a cancel never lands mid-run. The `sidecar` lane in PoC-2 streams over a socket; a streaming ASGI bridge is PoC-2 work if the in-memory lane must stream.

## Chassis events over A2A

One `Request` is one A2A task. The chassis is the A2A client; the template A2A server wraps `handle`. The mapping is the same in every lane: only the URL and the httpx transport differ. It uses a2a-sdk 1.2 (A2A protocol 1.0, JSON-RPC binding, server-sent events for streaming). The 1.x types are protobuf classes in `a2a.types`; there is no `DataPart` class, a `Part` has a `text`, `raw`, `url`, or `data` field.

### Request in

The connector sends one `SendMessageRequest` with `SendStreamingMessage`, always streaming; the collector folds the stream for `stream: false`.

| What | Where on the wire | The workload reads it with |
| ---- | ----------------- | -------------------------- |
| `input.text` | The first `Part` of the `Message`, a text part (`new_text_part`), only when set | `RequestContext.get_user_input()` |
| `input.data` | A data `Part` (`new_data_part(data, media_type="application/json")`), only when not empty | `get_data_parts(context.message.parts)[0]` |
| `ctx` (`context.v0.json`) | `SendMessageRequest.metadata["chassis.ctx"]`, as JSON | `RequestContext.metadata["chassis.ctx"]` |
| Schema version | `SendMessageRequest.metadata["chassis.schema_version"]` | `RequestContext.metadata["chassis.schema_version"]` |
| Trace | `ctx.trace_id`. PoC-2: the connector also sets the `traceparent` HTTP header with the same id; the `inprocess` connector sets no header today | PoC-2: the workload's HTTP client propagates it (ADR-001, item 6) |

`Message.role` is `ROLE_USER`. Nothing else travels: no key, no header of the chassis's own.

### Events out

Every chassis event is exactly one A2A stream event, in order. The event's full JSON, with its `schema_version`, travels in that A2A event's `metadata["chassis.event"]`. The connector reads only that key and validates it with `parse_event`. The parts are the A2A-native view of the same event, for generic clients such as an A2A inspector or another agent; the connector ignores them.

| Chassis event | a2a-sdk type | `TaskState` | Native parts | `TaskUpdater` call |
| ------------- | ------------ | ----------- | ------------ | ------------------ |
| `start` | `TaskStatusUpdateEvent` | `TASK_STATE_WORKING` | none | `update_status(WORKING, metadata=...)`. a2a-sdk 1.2 needs a `Task` on the queue before the first status update, so the server enqueues one (`TASK_STATE_SUBMITTED`, `new_task`) first; the connector ignores it, as it ignores every non-terminal update with no `chassis.event` |
| `delta` | `TaskArtifactUpdateEvent` | unchanged | One text part with `text`, on the artifact `output`; `append` is false on the first delta and true after; `last_chunk` is never set | `add_artifact([new_text_part(text)], artifact_id="output", append=..., metadata=...)` |
| `tool_call` | `TaskStatusUpdateEvent` | `TASK_STATE_WORKING` | `status.message`: an agent message with one data part `{call_id, name, arguments, result}` | `update_status(WORKING, message=new_agent_message([...]), metadata=...)` |
| `metrics` | `TaskStatusUpdateEvent` | `TASK_STATE_WORKING` | none | `update_status(WORKING, metadata=...)` |
| `end` | `TaskStatusUpdateEvent` | `TASK_STATE_COMPLETED`, for `ok`, `retry`, and `fallback` alike; the chassis status is inside the event | `status.message`: an agent message with one data part `output`, only when set | `update_status(COMPLETED, message=..., metadata=...)` |
| `error` | `TaskStatusUpdateEvent` | `TASK_STATE_FAILED` | `status.message`: an agent message with one text part `message` | `update_status(FAILED, message=..., metadata=...)` |

`metadata` is a protobuf `Struct`, so numbers cross as doubles: integers survive up to 2^53, and `12` comes back as `12.0`, which pydantic accepts for an `int` field. The contract suite checks that `metrics` round-trips its integers.

A2A states the connector meets that are not chassis events (suggested):

| A2A event | Becomes |
| --------- | ------- |
| `TASK_STATE_CANCELED` after the connector cancelled | Nothing: the stream ends cleanly |
| `TASK_STATE_CANCELED` otherwise | `error {code: "a2a.canceled", retryable: false}` |
| Terminal update with no `chassis.event` | `COMPLETED` gives `end {status: ok}`; `FAILED` or `REJECTED` gives `error {code: "a2a.failed"}` |
| Non-terminal update with no `chassis.event` | Ignored |
| `TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_AUTH_REQUIRED` | `error {code: "a2a.unsupported_state"}`; not in v0 |
| `chassis.event` that fails `parse_event` | `error {code: "a2a.bad_event", message}` |

On the server side, an exception from `handle` becomes `error {code: "workload.exception"}` and `FAILED`, and a yielded dict that fails the schema (or names an unsupported `schema_version`) becomes `error {code: "workload.bad_event"}` and `FAILED`, so the mapping stays one to one and the SDK's own failure path is never the first line of defense. The server enforces the order the port promises: `start` first, `end` or `error` last, nothing after: an event before `start` or a second `start` is `error {code: "workload.bad_order"}`, and a `handle` that returns without `end` or `error` is `error {code: "workload.no_end"}`. A request whose `metadata["chassis.schema_version"]` the server does not accept is `error {code: "a2a.unsupported_schema_version"}`.

The workload reaches a model through the chassis's proxy, `POST /v1/chat/completions` (`chassis.server.model_proxy`), OpenAI-compatible, streaming and complete, mapped onto `ModelPort`. It forwards no `Authorization` header and adds none; the model adapter holds the key. Request correlation and per-request budgets on the proxy come in PoC-2.

### Ids

| Chassis | A2A |
| ------- | --- |
| `request_id` | `metadata["chassis.ctx"].request_id` on the request, and `start.request_id` on the stream. The connector checks they match; a mismatch is `error {code: "a2a.request_mismatch"}` |
| `trace_id` | `Message.context_id`. A2A groups tasks by context; one trace is one context |
| `Message.message_id` | A new UUID per send. A retry of the same `request_id` is a new message and a new task |
| `Task.id` | Assigned by the server; a2a-sdk refuses a client-set id for a new task. The connector records it as the span attribute `a2a.task_id` and uses it for `CancelTaskRequest` |
| `idempotency_key` | Inside `ctx` only. The pipeline handles idempotency before the connector runs |

### Versions and cancel

- The chassis accepts the current and the previous major `schema_version`, on both `metadata["chassis.schema_version"]` and every `metadata["chassis.event"]`. A bump adds the new major to `SUPPORTED_SCHEMA_VERSIONS` and keeps the old one. The A2A protocol version is the `A2A-Version: 1.0` header, set by the client factory.
- A timeout or a client disconnect makes the connector send `CancelTaskRequest`. The server's `AgentExecutor.cancel` closes the `handle` generator (`aclose()`, or a flag the executor checks at its next event when the generator is mid-await) and publishes `TASK_STATE_CANCELED`. In the `inprocess` lane neither fires mid-run (see "`EngineConnector`, draft" above); they become live with the `sidecar` lane in PoC-2.

### Cost per lane

| Lane | Transport | What each event costs |
| ---- | --------- | --------------------- |
| `inprocess` | `httpx.ASGITransport(app)`, no socket | Serialization only: proto to JSON on the server, JSON to proto on the client. The events arrive after `handle` returns, in one body |
| `sidecar` | `http://127.0.0.1:<port>` | The same plus one SSE frame over loopback. PoC-2 measures it per delta |
| `remote` | The endpoint URL plus a credential interceptor | The same plus the network hop, TLS, and the per-remote credential |

## Where the template A2A server lives

Decided in [ADR-002](../planning/adr/002-template-a2a-server-placement.md). In short:

| Module | Holds | Imports from `chassis` |
| ------ | ----- | ---------------------- |
| `chassis.adapters.a2a.mapping` | The mapping above, as pure functions over dicts: `request_to_message(input, ctx)`, `message_to_input(context)`, `event_to_update(event, updater)`, `update_to_event(stream_response)` | Nothing. a2a-sdk only |
| `chassis.adapters.a2a.server` | The template server: `HandleExecutor(AgentExecutor)` wrapping a `handle`, `build_agent_card(...)`, `build_app(handle, card) -> FastAPI` | `chassis.core.events.parse_event`, to validate what the workload yields |
| `chassis.adapters.a2a.inprocess` | The `inprocess` connector: builds the app in the same process, calls it through `httpx.ASGITransport`, maps updates back to events | Anything it needs |
| `packages/workloads/echo-python` | The plug-in: `echo_python.handle`, plain Python, dicts in and out | Nothing, ever. It is a `uv` workspace member so the chassis's tests can import it; the dependency runs one way |

Config picks it: `spec.engine.connector: inprocess` and `spec.engine.handle: "echo_python:handle"`. `profiles.REGISTRY["engine"]["inprocess"]` builds the connector.

In PoC-2, the service template (025 H-10) ships `mapping.py` and `server.py` as `a2a_server.py` in the workload container, with `parse_event` replaced by `jsonschema` validation against the vendored `events.v0.json`. The chassis keeps its copy for `inprocess`. The contract suite runs every case over both (ADR-001, hard requirement 2), and a CI check diffs the two copies of `mapping.py` (suggested). Nothing of the chassis is installed in the workload.

## Profiles

`spec.adapters {model, engine, config, telemetry}`. Profiles `fake`, `local`, `cloud` set them all; an agent's own `spec.adapters` overrides per port. `build_ports(profile)` returns a `PortBundle` or raises `AdapterNotAvailable` naming the PoC that adds the missing adapter.
