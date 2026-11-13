# The chassis contract

## 1. What this is

This file states every rule of the chassis wire contract that is in force, once, with no history. The versions as each PoC wrote them are in `docs/contracts/`. Where this text and the code differ, the code wins.

The event schema is `schema_version: "0"` (`packages/chassis/schemas/events.v0.json`). The chassis accepts the current and the previous major (`SUPPORTED_SCHEMA_VERSIONS`, today `("0",)`). A breaking change bumps the major, keeps the old one accepted, and publishes a new schema file.

Values the epic does not give are marked `suggested:` where the source marks them so. Schema files are under `packages/chassis/schemas/`; modules under `packages/chassis/src/chassis/`.

## 2. The envelope

Source: `core/envelope.py`; `request.v0.json`, `response.v0.json`, `task_input.v0.json`, `context.v0.json`. Every model refuses unknown fields.

### `Request` (`request.v0.json`)

| Field | Type | Default | Notes |
| ----- | ---- | ------- | ----- |
| `request_id` | string | minted (UUID) | On `/v1/run` the body may leave it unset |
| `trace_id` | string | minted | The body, else a valid `traceparent` header, else minted |
| `idempotency_key` | string | minted | The `Idempotency-Key` header, else the body, else minted. An empty string counts as unset |
| `agent` | string | the served agent | Another value is refused (400) |
| `agent_version` | string | the served version | Another value is refused (400) |
| `input` | `TaskInput` | required | |
| `context_ref` | string or null | null | Accepted and passed through; unused |
| `stream` | bool | `false` | |
| `budget` | `Budget` | defaults | |

### `TaskInput` (`task_input.v0.json`)

| Field | Type | Default |
| ----- | ---- | ------- |
| `text` | string or null | null |
| `data` | object, open | `{}` |

A chat interface may set two keys in `data` (suggested: the names): `system` (string, the caller's system prompt) and `history` (`[{"role": "user" | "assistant", "text": string}]`). Both only when not empty. A native caller may send the same keys; `data` passes through as is. A caller may name the task in `data.task` (string, suggested: the key): the pipeline resolves it through `spec.model.routes` into `ctx.model_route` ([models](models.md)); the key stays in `data` for the workload.

### `Budget`

| Field | Type | Default | Rule |
| ----- | ---- | ------- | ---- |
| `max_tokens` | integer | 2000 | At least 1; at most `spec.limits.max_tokens_max` |
| `timeout_ms` | integer | 30000 | At least 1; at most `spec.limits.timeout_ms_max` |

`timeout_ms` is the run deadline and the per-read timeout of the lane (section 4).

### `Context` (`context.v0.json`)

What the chassis gives `handle` next to the input. It never holds a product or platform credential. Its one secret, `run_secret`, is a capability for the chassis's own remote listener, for this run only. Passed through the lane unchanged. A workload must ignore keys it does not know.

| Field | Type | Set by | Value |
| ----- | ---- | ------ | ----- |
| `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version` | string | the pipeline | As in the request |
| `budget` | `Budget` | the pipeline | The request's |
| `versions` | `Versions` | the pipeline | As on the response |
| `model_route` | string or null | the pipeline | the route of `input.data.task` through `spec.model.routes`, else `spec.model.route`; the fallback route on a gate fallback attempt (section 11) |
| `run_secret` | string or null | the pipeline, per run | `secrets.token_urlsafe(32)` (43 characters; `RUN_SECRET_BYTES`), minted fresh for every run in every lane (a retry is a new run with a new secret). Null in plain-A2A mode, which never sends `ctx`. Hidden from `repr`, kept in `RunRecord`, never in `StatePort`, scrubbed from every event the `remote` lane yields. Not a credential for anything but the run that holds it (section 7) |
| `traceparent` | string or null | the connector, per run | `00-<trace id>-<parent id>-01`. The trace id is `trace_id_hex(ctx.trace_id)`: `trace_id` as is when it is 32 lowercase hex, else the first 32 hex of its sha256. The parent id is the connector's run span id, else a fresh random 16 hex (suggested). Not a credential |

### `Response` (`response.v0.json`)

| Field | Type | Value |
| ----- | ---- | ----- |
| `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version` | string | As in the request |
| `output` | object | `{"text": ...}` from the joined deltas, or `end.output` when set, or `{"error": {code, message, retryable}}` |
| `metrics` | object | The summed `metrics` events: tokens, cost, `attempts` |
| `status` | `ok` \| `retry` \| `fallback` \| `error` | `end.status`, or `error` when the last event is `error` |
| `versions` | `Versions` | |
| `context_ref` | string or null | As in the request |

### `Versions`

| Field | Type | Value |
| ----- | ---- | ----- |
| `chassis` | string, required | `CHASSIS_VERSION` |
| `config` | string or null | The config version: `<version>+<hash12>` when the store document sets `version`, else `<hash12>`, the first 12 hex of the sha256 of the parsed document as sorted-key JSON. A bootstrap file read without a store keeps its own `version` |
| `prompt` | string or null | `spec.prompt.version` |
| `model_route` | string or null | `spec.model.route`, the configured route, also when the fallback answered |

## 3. The events

Source: `core/events.py`; `events.v0.json`. A discriminated union on `type`. Every event carries `schema_version: "0"`; a yielded event with none is filled with `"0"`. Unknown fields are refused by every reader.

| Event | Fields | Meaning |
| ----- | ------ | ------- |
| `start` | `request_id` | First, once. Must equal the request's `request_id` |
| `delta` | `text` | A piece of the answer; deltas join to the full text |
| `tool_call` | `call_id`, `name`, `arguments` (object, default `{}`), `result?` (object) | A tool the workload called through the chassis |
| `metrics` | `input_tokens` (0), `output_tokens` (0), `cost_usd?`, `model_route?`, `latency_ms?`, `attempt` (1) | Usage per attempt; the collector sums tokens and cost |
| `end` | `status: ok \| retry \| fallback` (default `ok`), `output?` (object) | Last on success. `output` replaces the joined text when set |
| `error` | `code`, `message`, `retryable` (default `false`) | Last on failure; the response gets `status: error` |

**Order.** `start` first and once; `end` or `error` last; nothing after. The template server enforces it. A failure before `handle` starts is a single `error` with no `start`. `end.status` is `ok`, `retry`, or `fallback`; the A2A state is `COMPLETED` for all three.

**The chassis's error codes.** Every code below ends the run as one `error` event. Server codes come with `TASK_STATE_FAILED`.

| Code | Emitted by | When | `retryable` |
| ---- | ---------- | ---- | ----------- |
| `a2a.unsupported_schema_version` | server | `chassis.schema_version` is not accepted; `handle` does not run | false |
| `a2a.bad_request` | server | `chassis.ctx` or `chassis.input` is not a JSON object | false |
| `workload.bad_event` | server | A yielded value fails the schema, names an unsupported version, or holds `NaN` or `Infinity` | false |
| `workload.bad_order` | server | An event before `start`, or a second `start` | false |
| `workload.no_end` | server | `handle` returned without `end` or `error` | false |
| `workload.exception` | server | `handle` raised | false |
| `a2a.bad_event` | connector | `chassis.event` is not a JSON object or fails `parse_event` | false |
| `a2a.request_mismatch` | connector | `start.request_id` is not the request's | false |
| `a2a.timeout` | connector | The run deadline passed, or one read, connect, write, or pool wait took longer than `budget.timeout_ms` | true |
| `a2a.transport` | connector | The connection failed or dropped, the a2a-sdk client failed, or the stream ended with no `end` or `error` | true |
| `a2a.canceled` | connector | The task reached `CANCELED` by another hand | false |
| `a2a.failed` | connector | A terminal `FAILED` or `REJECTED` with no `chassis.event` | false |
| `a2a.unsupported_state` | connector | `INPUT_REQUIRED` (an agent run never waits for a person, [ADR-015](../planning/adr/015-interrupts-end-the-run.md)) or `AUTH_REQUIRED` (the workload holds no credential and never needs one from a caller, ADR-001 hard requirement 1) | false |
| `engine_error` | pipeline | The connector raised instead of yielding an `error` | false |
| `guardrail_blocked` | pipeline, stream guard | A guard stage blocked the input or the answer (section 8) | false |
| `guardrail_unavailable` | pipeline | The guardrail port failed and `on_unavailable` is `fail_closed` | true |
| `evaluator_gate_failed` | gate | Every attempt failed and there is no fallback route (section 11) | false |
| `idempotency_in_progress` | pipeline | This call's idempotency claim was lost mid-run (section 6) | true |

A workload's own codes are a convention, not in the schema. Codes are open strings: a new code is compatible. The codes above keep their `retryable`.

## 4. `handle(input, ctx)` and the A2A mapping

```python
async def handle(input: dict, ctx: dict) -> AsyncIterator[dict]: ...
```

Every workload implements it. It yields event dicts (or objects with `model_dump(mode="json")`). It is served over A2A in every lane. A workload never installs the chassis package.

- **What `handle` gets.** `input` is exactly `chassis.input`, `{text, data}`. `ctx` is exactly `context.v0.json`. Integers stay integers both ways.
- **What `handle` forwards.** `ctx["traceparent"]`, when set, as the `traceparent` header on every model proxy call and every MCP request. A child span id is fine: the proxies key on the trace id only (`core/trace.py`, `parse_traceparent`). `ctx["run_secret"]`, when set, as the `x-chassis-run-secret` header on the same calls. It never logs the secret. The `sidecar` loopback proxy ignores the header; the remote listener requires it (section 7).
- **Where it calls.** Only the chassis's proxy listeners: `CHASSIS_MODEL_URL` (default `http://127.0.0.1:8090/v1`, suggested: the port) and `CHASSIS_TOOL_URL` (`http://127.0.0.1:8090/mcp`). A remote workload calls the remote listener instead.
- **No key, no retry.** A workload holds no key and sends no `Authorization` of its own; an SDK that insists on one gets a placeholder. Retries and fallback are the chassis's.
- **Safe to run twice.** The evaluator gate may rerun a request; write tools dedupe by the run's key (section 7).
- A workload must ignore unknown keys in `ctx` and `input`.

### The agent card and the task lifecycle

One `Request` is one A2A task. The chassis is the A2A client; the template server (`adapters/a2a/server.py`, copied as `packages/workload-a2a`) wraps `handle`. a2a-sdk 1.x, A2A protocol 1.0, JSON-RPC at `/`, server-sent events.

- The connector reads the agent card at `setup` (`/.well-known/agent-card.json`). The `sidecar` lane refuses a card whose interface URL is not loopback. The `remote` lane keeps only the JSON-RPC interface and pins its URL to `spec.engine.url`; a card with none is refused. `inprocess` builds the card from `spec.engine.card`.
- The connector sends one `SendStreamingMessage`, always streaming; the collector folds it for `stream: false`. `Message.role` is `ROLE_USER`; `message_id` is a new UUID per send; `context_id` is `trace_id`. The server enqueues a `SUBMITTED` task first; the connector takes `Task.id` from it (span attribute `a2a.task_id`) and uses it for `CancelTask`.
- **Timeout and cancel.** `budget.timeout_ms` is the httpx timeout per operation and the deadline for the whole run, checked on every read. On any early end (deadline, transport, bad event, mismatch, a client that left) the connector closes its stream, then sends `CancelTask` with the run's `traceparent`, best effort, within `CANCEL_TIMEOUT_S` (5 s, suggested). The server's `cancel` sets a flag, publishes `CANCELED`, and closes the generator in its own task. The task store is pruned once a task is terminal.
- `probe() -> bool` GETs the card (`sidecar`: 1 s; `remote`: `spec.engine.probe_timeout_s`). True on 200 only, never an exception.

### How events ride A2A (`adapters/a2a/mapping.py`)

Chassis JSON crosses as a string, so numbers survive every SDK byte for byte: `json.dumps(value, separators=(",", ":"), allow_nan=False)`. The v0 `Struct` form is still read; no writer produces it.

| Key | Where | Holds |
| --- | ----- | ----- |
| `chassis.input` | `SendMessageRequest.metadata` | The whole `TaskInput` as a JSON string; always set |
| `chassis.ctx` | `SendMessageRequest.metadata` | The `Context` as a JSON string |
| `chassis.schema_version` | `SendMessageRequest.metadata` | `"0"`, a plain string; missing is `"0"` |
| `chassis.event` | Every A2A stream event's `metadata` (the artifact's for `delta`) | The event, with its `schema_version`, as a JSON string |

Native parts are the view for generic clients and are read only when `chassis.input` is missing: `input.text` as the first text part, `input.data` as a data part (`application/json`, doubles).

| Chassis event | A2A event | `TaskState` | Native parts |
| ------------- | --------- | ----------- | ------------ |
| `start` | `TaskStatusUpdateEvent` | `WORKING` | none |
| `delta` | `TaskArtifactUpdateEvent`, artifact `output` | unchanged | One text part; `append` false on the first, true after |
| `tool_call` | `TaskStatusUpdateEvent` | `WORKING` | A data part `{call_id, name, arguments, result}` |
| `metrics` | `TaskStatusUpdateEvent` | `WORKING` | none |
| `end` | `TaskStatusUpdateEvent` | `COMPLETED` | A data part `output`, when set |
| `error` | `TaskStatusUpdateEvent` | `FAILED` | A text part `message` |

A terminal update with no `chassis.event` becomes `end {status: ok}` (`COMPLETED`) or `error a2a.failed`. A non-terminal one with no `chassis.event` is ignored.

### Headers on every A2A request of a run

`traceparent`, equal to `ctx.traceparent`, in every lane. `baggage` (W3C) with `user.id` and `session.id` when the telemetry adapter has them (section 9). In the `remote` lane, `Authorization: Bearer <token>`. `A2A-Version: 1.0`. Nothing else: no key, no caller header. The card fetch belongs to no run and carries no `traceparent`. The workload sends back only the A2A stream; it never sees a key, and no header of its own reaches the caller.

### The three lanes

`spec.engine.connector` is the one lane key (default `sidecar`). `spec.adapters.engine` is refused. The lane is checked at load, in `build_ports`, and in the lifespan.

| Lane | Transport | `spec.engine` keys | Where allowed |
| ---- | --------- | ------------------ | ------------- |
| `inprocess` | `httpx.ASGITransport`, no socket; events arrive in one batch after `handle` returns, so no mid-run cancel | `handle` (`module:attribute`, required), `card?` `{name, version, description}` | `fake` and `local` only |
| `sidecar` | `http://127.0.0.1:<port>` or `http://localhost:<port>` (scheme `http`, a port, nothing else), or `uds` (a Unix socket; `url` still fills `Host`) | `url` (required), `uds?` | every profile |
| `remote` | `http(s)://host:port[/path]` over the network; `https` in `cloud`; `uds` refused in `cloud` | `url`, `auth` (required), `uds?`, `probe_timeout_s` (2.0), `protocol`, `a2a` | every profile; required for `spec.trust: untrusted` |

`spec.engine.auth`: `{scheme: bearer, token_env, previous_token_env?}`. Only `bearer` today; mTLS is not built. `token_env` names an environment variable (`^[A-Z_][A-Z0-9_]*$`); the config never holds a token. The connector reads it at `setup`, sends it on the card fetch, every message, the cancel, and the probe, follows no redirect, ignores proxy variables (`trust_env=False`), and never logs it. `RemoteConnector` replaces each exact occurrence of the token in every event it yields with `[redacted]`. `auth` is refused for the other lanes. The remote workload checks the bearer (`workload-a2a serve --require-token-env`, `--previous-token-env` for rotation); a bad token is one fixed 401.

Transport error text: in chassis mode `a2a.timeout` and `a2a.transport` carry `str(exc)`; in plain mode they carry fixed text (`the remote timed out`, `the remote failed`) and the exception goes to the log.

### Plain-A2A mode (`adapters/a2a/plain.py`)

`spec.engine.protocol: a2a` (default `chassis`), `remote` only, for a third-party agent that emits no `chassis.event`. The request carries a text part, a data part when `data` is not empty, no `chassis.*` metadata, and `context_id` per `spec.engine.a2a.context_id` (`omit` default, or `trace_id`). The translator reads text parts only, never `chassis.event` or `task.history`:

| A2A item | Becomes |
| -------- | ------- |
| any first item | `start {request_id}` |
| `WORKING` status with text; artifact update that is not a `last_chunk` snapshot | `delta` |
| `last_chunk` artifact without `append` | a snapshot, kept for the end |
| `COMPLETED` (status, task, or a bare `message`) | `metrics`, then `end ok`; `output: {"text": ...}` only when no delta was sent |
| `FAILED`, `REJECTED` | `metrics` if usage was seen, then `error a2a.failed`, fixed text |
| `CANCELED` | `error a2a.canceled` |
| `INPUT_REQUIRED`, `AUTH_REQUIRED` | `error a2a.unsupported_state`; the task is cancelled ([ADR-015](../planning/adr/015-interrupts-end-the-run.md)) |

Usage comes from the metadata key `spec.engine.a2a.usage_key` (default null, zeros), aliases `promptTokenCount` / `prompt_tokens` / `input_tokens` / `inputTokens` and the output twins; the last value wins. `tool_call` never appears. The span gets `a2a.protocol` and `a2a.usage_known`.

## 5. The public interfaces

All on the public listener (`--host 127.0.0.1 --port 8080`). Each HTTP interface is one OpenAPI operation marked `x-chassis-interface` (suggested: the name). `spec.interfaces {openai, anthropic, mcp}` switches the non-native ones (each `true` by default); native is always on. The workload never learns the interface.

| Interface | Route | Body | Answer | Streams |
| --------- | ----- | ---- | ------ | ------- |
| `native` | `POST /v1/run` | `RunRequest`: the `Request` with ids and agent optional, strict JSON types (422 on a wrong type) | The `Response` | One `event: <type>` frame per chassis event, then a `response` frame |
| `openai` | `POST /v1/chat/completions` (public port) | OpenAI `CompletionCreateParams`; `model` is the agent's name | `ChatCompletion`, `id: chatcmpl-<request_id>` | `ChatCompletionChunk` frames, then `data: [DONE]` |
| `anthropic` | `POST /v1/messages` (public port) | Anthropic `MessageCreateParams`; `model` is the agent's name | `Message`, `id: msg_<request_id>` | `message_start` to `message_stop`, no `ping` |
| `mcp` | `/v1/mcp`, streamable HTTP, stateless | One tool named after the agent; arguments are the `RunRequest` body | The native envelope as the tool result | No, complete only |

Also: `GET /manifest`, `GET /health`, `GET /ready`, `GET /metrics`, `GET /openapi.json`, `/docs`, `/redoc`, `POST /v1/feedback` (section 10). `/v1/models`, `/v1/responses`, and `/v1/messages/count_tokens` are 404.

**A chat format onto the canonical request** (`adapters/openai_compat`, `adapters/anthropic_compat`): the last message must be `role: user` and becomes `input.text` (text parts joined with `"\n"`); system and developer messages become `input.data.system`; earlier user and assistant turns become `input.data.history`; `model` must equal the agent's name (404 otherwise); OpenAI `max_completion_tokens` or `max_tokens` (both set and different is 400; missing is 2000) and Anthropic `max_tokens` become `budget.max_tokens`; `stream` is `request.stream`; `budget.timeout_ms` is 30000. Anything else is refused (400), or accepted, ignored, and counted (`chassis.inbound_ignored{interface, param}`).

**The answer.** `answer_text` (`core/inbound.py`) is `output.text` when it is a string, else the output as compact JSON with sorted keys. OpenAI complete: one choice, `finish_reason: stop`, `usage` as the run's totals. OpenAI stream: a role chunk, one chunk per `delta`, the rest of the answer as one chunk when it extends the deltas, a finish chunk with `usage` (or, with `stream_options.include_usage`, a trailing `choices: []` chunk), then `[DONE]`. Anthropic complete: one text block or `content: []`, `stop_reason: end_turn`. Anthropic stream: `message_start`, `content_block_start` at the first text, one `content_block_delta` per `delta` and for the rest, `content_block_stop`, `message_delta` with `end_turn` and usage, `message_stop`. When the deltas are not a prefix of the answer, nothing more is sent. A chat stream sends nothing until the first `delta` or the terminal event; an `error` first is an HTTP error; an error after text is one error frame. `tool_call` events are never client tool calls. With marking on (section 8) the OpenAI and Anthropic bodies carry `ai_marking: {ai_generated: true, disclosure_required: true}` (complete, every chunk, and `message_start`).

**`spec.limits`** bounds every interface: `max_tokens_max` (8000), `timeout_ms_max` (120000), `messages_max` (256; chat `messages`, or `history` plus the input), each 400 `limit_exceeded`; `body_bytes_max` (1 MiB), 413 before parsing, in the route's own shape. Refused, never clamped.

### The status table

OpenAI body: `{"error": {"message", "type", "param", "code", "retryable"}}`. Anthropic body: `{"type": "error", "error": {"type", "message": "<code>: <text>"}, "request_id"}`. Both send `x-should-retry`. Native and `/v1/feedback` refusals: `{"detail": {"code", "message"}}` or `{"detail": str}`. The texts are fixed per code (`PUBLIC_MESSAGES`, `server/security.py`, `server/feedback.py`, `server/rate_limit.py`); a run's own message never reaches a chat client. Native and MCP answer a run's `error` as a 200 envelope with `status: error` and the run's own message.

| HTTP | Code | When | OpenAI `type` | Anthropic `error.type` |
| ---- | ---- | ---- | ------------- | ---------------------- |
| 400 | `invalid_body`, `unsupported_parameter`, `unsupported_message` | A refused body (chat formats); `{"detail"}` on native for another agent | `invalid_request_error` | `invalid_request_error` |
| 400 | `limit_exceeded` | Over a `spec.limits` ceiling | `invalid_request_error` | `invalid_request_error` |
| 401 | `unauthenticated` | No or bad token; `WWW-Authenticate: Bearer error="invalid_token"` | `authentication_error` | `authentication_error` |
| 403 | `forbidden` | A token without the route's scope; `WWW-Authenticate: Bearer error="insufficient_scope", scope="<scope>"` | `permission_error` | `permission_error` |
| 404 | `model_not_found` | `model` is not the agent | `invalid_request_error` | `not_found_error` |
| 409 | `trace_id_in_use` | A native body `trace_id` another in-flight run holds | | |
| 409 | `idempotency_in_progress` | Same fingerprint still in flight after this call's `budget.timeout_ms`; the claim was lost; a too-large or unknown-version entry (not retryable) | `server_error` | `api_error` |
| 413 | `limit_exceeded` | Body over `body_bytes_max` | `invalid_request_error` | `invalid_request_error` |
| 422 | | Native: a body that fails strict validation (FastAPI's shape), a budget below 1 | | |
| 422 | `idempotency_conflict` | The key holds another fingerprint | `invalid_request_error` | `invalid_request_error` |
| 429 | `rate_limited` | Past the caller's token bucket; `Retry-After: <seconds>` | `rate_limit_error` | `rate_limit_error` |
| 499 | | The client left a complete call; empty body | | |
| 500 | `engine_error`, `internal_error` | The connector raised; the chassis failed after the run opened (`x-should-retry: false`) | `server_error` | `api_error` |
| 502 | any other non-retryable run code (`guardrail_blocked`, `evaluator_gate_failed`, `a2a.failed`, ...) | | `server_error` | `api_error` |
| 503 | `not_ready`, `state_unavailable`, `auth_unavailable`, `guardrail_unavailable`, any other retryable code | Not ready; the store failed on a keyed call; the IdP's keys; `Retry-After: 1` on auth | `server_error` | `overloaded_error` |
| 503 | `overloaded` | This replica is at `spec.limits.max_in_flight` runs in flight (CH-15); `Retry-After: 1`; the workload never saw the call; native: `{"detail": {"code", "message"}}` | `server_error` | `overloaded_error` |
| 504 | `a2a.timeout` | The run passed its deadline | `server_error` | `timeout_error` |

`status_for` (`core/inbound.py`): a code not named above is 503 when retryable, else 502.

### Headers

| Header | Direction | Where | Value |
| ------ | --------- | ----- | ----- |
| `Authorization: Bearer` | in | every public route but `/health`, `/ready` | The caller's token (section 8). Removed before the route |
| `traceparent` | in | every interface | The run's trace id when valid and the body sets none; dropped when it names a trace in use (re-minted, `chassis.trace_id_reminted{interface}`) |
| `Idempotency-Key` | in | every interface | Wins over the native body's key |
| `x-chassis-interface` | none | none | Not a request header. It is the OpenAPI extension that marks each interface operation for the manifest. A caller who sends it on `/v1/run` is ignored: the call is native |
| `x-chassis-run-secret` | in | remote listener only | The run's `ctx.run_secret`, on every model and `/mcp` call (section 7). Removed before the route; never logged |
| `x-session-id` (`spec.observability.session_header`) | in | run routes | `session.id` on the run span |
| `x-api-key`, `anthropic-version`, `anthropic-beta`, `baggage` | in | chat formats | Accepted and ignored; never forwarded or logged |
| `x-request-id`, `x-trace-id`, `request-id` (Anthropic) | out | OpenAI, Anthropic | The run's ids |
| `x-chassis-status` | out | OpenAI, Anthropic, complete 200 | `ok`, `retry`, or `fallback` |
| `x-should-retry` | out | OpenAI, Anthropic errors | From `retryable` |
| `Idempotent-Replayed: true` | out | native, OpenAI, Anthropic | A replayed answer |
| `X-AI-Generated`, `X-AI-Disclosure-Required`, `Access-Control-Expose-Headers` | out | run routes | Section 8 |
| `Connection: close` | out | public listener while draining | Section 6 |

### MCP (`adapters/mcp/agent.py`)

Generated with `FastMCP.from_openapi` over the `/v1/run` operation alone. One tool named after the agent; its input schema is the `RunRequest` body. The call goes in process to `POST /v1/run` marked by the ASGI scope key `chassis.mcp_hop` (`MCP_HOP_SCOPE_KEY`, set to `True` on a copy of the scope by `_mark_mcp_hop`; a server never sets a scope key from a header, so only this hop carries it). The flag reads `stream` as false, skips the disconnect watch, labels telemetry `mcp`, and re-mints a body `trace_id` already in use instead of answering 409. It grants nothing. The call forwards only `traceparent`, `tracestate`, `Idempotency-Key`, and the caller's `Authorization`; every other caller header is dropped. A run with `status: error` is a normal tool result; only a non-2xx is an MCP tool error. 503 before and after the lifespan. Not watched for a client that leaves.

### `GET /manifest` (`manifest.v0.json`, `core/manifest.py`)

Built per request. suggested: the whole shape.

| Field | Value |
| ----- | ----- |
| `manifest_version` | `"0"` |
| `agent` | `{name, version, trust}`; `trust` is `spec.trust` (default `trusted`) |
| `versions` | As on every response |
| `lane` | `spec.engine.connector` |
| `agent_class` | `spec.class`, or null when the config names none (008 H-14) |
| `kind` | `spec.kind`, or null when the config names none (008 H-14) |
| `modules` | The optional B.5 modules the flags switch on, sorted (today `evaluator_gate`, `result_events`); the always-on modules of the class are implied, not listed (008 H-14) |
| `event_schema_versions` | `SUPPORTED_SCHEMA_VERSIONS` |
| `interfaces` | Native first, then each switched-on OpenAPI operation `{name, method, path, operation_id, streaming, model}`, then MCP `{name: "mcp", path: "/v1/mcp", transport: "streamable-http", streaming: false, tools}` |
| `openapi` | `{path, version, sha256}`; the sha256 of the spec as sorted-key JSON with no spaces |

### `/health`, `/ready`, `/metrics`

- `GET /health`: 200 while the process serves HTTP, draining included. It never looks at the workload.
- `GET /ready`: 200 `{"status": "ready"}` when the lifespan finished, the first config is loaded, the chassis is not draining, and the workload answers its probe; else 503 `{"status": "not ready", "reason": starting | draining | workload_unreachable}`. The monitor probes every 2 s with a 1 s timeout; 3 failures in a row make the workload unhealthy, one success heals it (suggested: the numbers; `server/readiness.py`).
- `GET /metrics`: Prometheus text 0.0.4, per replica, every sample labeled `agent`. Not in the OpenAPI document. With `spec.observability.metrics_scope` unset it needs no token (suggested); with a scope set, 401 without a token and 403 without the scope. The `cloud` profile sets it to `spec.security.read_scope` (`agent:read`) when the document names none (CH-14, P-4).

## 6. Idempotency, leases, readiness, drain, config, state and events

### Idempotency (`server/idempotency.py`)

A call with a key the client sent (header or native body) runs once per agent and key, on every interface, when `spec.idempotency.enabled`. A minted key never touches the store. The check runs after `to_request` and the limits, before the run opens.

- **Store key:** `chassis:idem:v1:<agent>:<sha256(key) hex>` (suggested). **Fingerprint:** sha256 of the canonical JSON of `{agent, input, context_ref}`; not the ids, `stream`, `budget`, `agent_version`, or the interface.
- **Claim:** `set_if_absent` of `{"v": 1, "state": "running", "owner", "fingerprint"}` with `ttl_s = lease_s`. Renewed by `compare_and_set` every `lease_s / 3`. The claim is lost when a renew finds another entry, or when no renew got through for `0.8 * lease_s` (`FENCE_FRACTION`, suggested). A lost claim cancels the run and answers 409 `idempotency_in_progress`, retryable; later in a stream it is one `error` event; nothing is cached or published.
- **A duplicate** with the same fingerprint polls every `wait_poll_ms` until the entry is `done` (replay) or gone (takeover: it claims and runs with what is left of its own `budget.timeout_ms`, at least 500 ms), or its own timeout passes (409).
- **Cached:** only a run whose last event is `end`, as `{"v": 1, "state": "done", "fingerprint", "request", "events", "response"}` for `ttl_s`. Over `max_entry_bytes` a `too_large` marker is stored and a repeat is 409, not retryable. An `error`, a client that left, a chassis failure, or `trace_id_in_use` frees the key. An entry with another `v` is 409, not retryable.
- **Replay:** no run, no model call, no result event, no trace-id check. The stored response and events, bound to the original ids, in this call's interface and mode; OpenAI `created` is fresh. `Idempotent-Replayed: true`.
- A keyed call fails closed: 503 `state_unavailable` when the store fails (suggested).
- A client that leaves a complete call (polled every 250 ms, suggested) cancels the run, frees the budget and the key, and gets an empty 499. MCP is not watched.

### Readiness and drain (`server/readiness.py`, `server/lifecycle.py`)

The chassis handles SIGTERM and SIGINT itself. `chassis serve --drain-delay-s` (5) and `--drain-timeout-s` (30), suggested. On the first signal, in order: `/ready` answers 503 `draining` and every public response carries `Connection: close`; the `on_drain` hooks run; sleep the delay; the public listener closes and in-flight requests get up to the drain timeout, then are cancelled (their tasks cancelled, their keys freed); pending result events and evaluator scores get up to 5 s; the ports close; the proxy listeners close last. A second signal exits at once. The proxy listeners bind first; a taken proxy port exits 3. `/ready` is 503 `starting` until the workload is reachable, for up to `--startup-wait-s` (120, suggested). The workload must get SIGTERM after the chassis has drained (`workload-a2a serve --drain-timeout-s`).

Backpressure (CH-15): a replica at `spec.limits.max_in_flight` runs in flight (32, suggested) refuses the next call on every public interface with 503 `overloaded` and `Retry-After: 1` before the engine sees it; `/ready` stays 200 and the drain above is unchanged (an overloaded replica is healthy, a draining one is not). Counted `chassis.requests_refused{reason="overloaded"}`; the gauge `chassis_runs_in_flight` is what the chart's HPA scales on (`hpa.runsInFlight`). The cap reloads with `spec.limits`. Added in [contract v9](../contracts/contract-v9.md), D.

Container order (`sidecar` lane, [ADR-012](../planning/adr/012-drain-order-on-rollout.md)): the workload is the native sidecar (an init container with `restartPolicy: Always`) and the chassis the main container, so the kubelet sends the chassis SIGTERM first and the workload only after the chassis has exited. The pod's grace is at least drain delay + drain timeout + 5 s.

### Config (`server/config.py`, `server/config_loader.py`)

The bootstrap file (`chassis serve --config`) is a whole `ChassisConfig`. With `spec.adapters.config: memory` it is the config. With `minio` or `s3` the store document `<agent.name>` (`<CONFIG_S3_PREFIX><agent.name>.yaml` in `CONFIG_S3_BUCKET`) is the config; at start it may differ from the bootstrap only in reloadable paths, else startup fails with `ConfigRejected`. The S3 adapter refuses a document over 256 KiB, with a YAML alias, or nested deeper than 64.

`RELOADABLE`: `version`, `spec.limits` (all of it, the rate limit and the uncorrelated cap included), `spec.model.route`, `spec.model.routes`, `spec.model.judge_route`, `spec.prompt`, `spec.idempotency.ttl_s`, `spec.harness.evaluator_sample_rate`. **Every other path is restart-only**: a document that changes one is refused with `restart_required` and the last good config stays. The swap is one assignment; a run keeps the `Context` it opened with. `/manifest` shows the active `versions.config`; `/openapi.json` keeps the start values. Counters: `chassis.config.reloaded`, `chassis.config.rejected{reason}`, `chassis.config.poll_failed{reason}`.

### `StatePort` (`ports/state.py`)

`get`, `set(ttl_s?)`, `set_if_absent(ttl_s)` (atomic, TTL required), `compare_and_set(expected, value | None, ttl_s?)` (atomic, byte-for-byte; `None` deletes), `delete`, `aclose`. Values are bytes; keys at most 512 bytes; `ttl_s` greater than 0. Every failure is `StateUnavailable`, with no credential in the message. Adapters: `memory`, `valkey` (`VALKEY_URL`, `VALKEY_USERNAME?`, `VALKEY_PASSWORD?`). Keys the chassis writes: `chassis:idem:v1:...`, `chassis:feedback:v1:req:...`, `chassis:feedback:v1:id:...`, `ratelimit:<agent>:<sha256(subject)[:32]>`.

### `EventPort` (`ports/events.py`)

`publish(topic, event)`, `subscribe(topic, handler, *, group, max_attempts=3)`, `aclose`. Adapters: `none` (default in every profile; a publish is dropped), `memory`, `kafka` (`KAFKA_BOOTSTRAP_SERVERS`), `dapr` (`DAPR_API_TOKEN`, `APP_API_TOKEN`; subscriptions served on the proxy app only). Picked by `spec.adapters.events`, never in code.

`CloudEvent`: CloudEvents 1.0, structured JSON, `application/cloudevents+json`, the same bytes on every broker. Attributes: `specversion: "1.0"`, `id`, `source`, `type`, `subject?`, `time`, `datacontenttype: "application/json"`, `dataschema?`, `data`. Extensions: `traceparent`, `idempotencykey`, `configversion`, `modelroute`, `partitionkey`, `aigenerated` (`true` when marking is on, else absent), and on the dead-letter topic only `deadletterreason` (the exception class name) and `deadletterattempts`. Publish returns once the broker has the event, else `PublishFailed`. Delivery is at least once; a handler that raises gets the event again up to `max_attempts`, then it goes to `<topic>.dlq`. Events with one `partitionkey` reach one group in order. Consumers dedupe by `id`.

**Result events** (`server/results.py`, `spec.events.result_events: true`): `agents.task.completed.v1` after `ok`, `retry`, or `fallback`; `agents.task.failed.v1` after `error`. `id` is `uuid5(EVENT_ID_NAMESPACE, "<type>:<request_id>")` hex; `source` `/agents/<agent>`; `subject` the `request_id`; `dataschema` `task-result.v1.json`; `idempotencykey` and `partitionkey` the sha256 hex of the run's key, never the key. Payload `TaskResult` (`core/results.py`): `{agent, agent_version, request_id, idempotency_key (hashed), status, input, output, usage, versions}`. Published in a background task, never in the request's path. Nothing is published for a replay, a refused call, or a run that did not finish. Counters: `chassis.events.published{topic}`, `chassis.events.publish_failed{topic}`, `chassis.events.publish_abandoned`. `spec.events.consume` is accepted only as null.

## 7. The model proxy and the tool proxy

Two uvicorn servers in one loop. The proxy listener (`--proxy-host 127.0.0.1 --proxy-port 8090`, suggested; loopback only unless `--allow-any-proxy-host`) serves `POST /v1/chat/completions`, `POST /v1/messages`, and `/mcp`, with no caller auth. The remote listener (`--remote-proxy-host <pod IP> --remote-proxy-port 8091`, suggested; one IP, never loopback or a wildcard) serves exactly the same three routes behind two middlewares, outermost first: `BearerAuth` (401 `remote_unauthenticated`, `authentication_error`, `missing or invalid bearer token`, `www-authenticate: Bearer`; constant-time compare against the current and previous token) and `RequireRun` (403 `run_required`, `permission_error`, `the traceparent names no run in flight`; see "The per-run secret" below). Every other path is 404 after both checks; a websocket is closed with 1008. Both listeners share one `state`, one run registry, and one uncorrelated cap. The public port never carries the proxies. Before the lifespan every proxy route answers 503.

### The per-run secret (`server/remote_auth.py`, `RequireRun`)

The bearer proves the caller is a remote workload. The run secret proves the call belongs to one run in flight. `RequireRun` runs after `BearerAuth` on every remote-listener call, in this order:

1. More than one `traceparent` header: refused, reason `traceparent_count`. It is never guessed, because `RequireRun` and FastMCP would read different headers.
2. The `traceparent` must name a run in `state.runs`. Else reason `no_run`. This path also runs one `hmac.compare_digest` against a stand-in, so the time does not show whether a trace id names a run.
3. Plain-A2A mode (`spec.engine.protocol: a2a`, set by config, never by the caller) with a run that holds no secret: the call passes on the traceparent alone. Counted as `chassis.remote.run_secret_skipped{reason="lane_without_secret"}` (suggested).
4. Otherwise exactly one `x-chassis-run-secret` header must match the run's secret under `hmac.compare_digest`. None is `secret_missing`. More than one, a value over `RUN_SECRET_MAX` (128 bytes, suggested), a run with no secret, or a mismatch is `secret_wrong`.

Every reason is 403 `run_required` with the same body bytes (the Anthropic body on `/v1/messages`), so a caller cannot tell "no such run" from "not your run". Counted as `chassis.remote.run_required{reason}`. A call that passes has the `x-chassis-run-secret` header removed before the route sees it. The `sidecar` loopback proxy has no `RequireRun`; the secret is minted and sent there but never checked.

Limit: the secret separates runs only between processes that share no memory and no `/proc`. A remote pod that serves several runs holds every run's secret. Full isolation needs one run per pod (core-review proposal R6-11, not built).

### `POST /v1/chat/completions` (`server/model_proxy.py`)

OpenAI-compatible onto `ModelPort`. Taken: `model` (the route), `messages`, `temperature` (suggested: 0.0 when unset), `max_tokens`, `tools` (function tools), `stream`. Every other key is accepted and ignored; the last streamed chunk always carries `usage`. Each message maps to one `ModelMessage {role: system | user | assistant | tool, content, name, tool_calls, tool_call_id}`: text content as is, a list of text parts joined with `"\n"`, `content: null` only on an assistant message with `tool_calls`, `tool_calls[i].function.arguments` must parse to an object, a tool message needs `tool_call_id`. Any other part type, role, or valued unknown key is 400 `unsupported_message` with `param: messages[<i>].<field>`, before the model is called. Out: `chat.completion` with `content`, `tool_calls` (arguments as a JSON string), `finish_reason`, `usage`; streamed: a role chunk, a chunk per text piece and per tool call, a last chunk with `finish_reason` and `usage`, then `[DONE]`.

**Dropped, never forwarded:** `Authorization`, `x-api-key`, every inbound header. The model adapter holds the key.

### `POST /v1/messages` (`server/model_proxy_messages.py`, `adapters/anthropic_compat/model_wire.py`)

Anthropic Messages onto the same port and admission, for a workload that speaks that format. The query string is ignored. Body cap 4 MiB (413 `body_too_large`). Mapped: `model` (the route; at most 256 characters), `messages` (A.5.1 rules: `system` entries stay in place; `tool_use` becomes a tool call; `tool_result` becomes a `tool` message bound to the nearest earlier `tool_use`; the last message may not be `assistant`), `system` (string or text blocks; a billing-header block is dropped), `tools` (`input_schema` with top-level `$schema` removed; `type` must be absent or `custom`), `tool_choice` `auto` or `none`, `max_tokens` (required), `stream`, `temperature` (0 to 1). Dropped and counted (`chassis.model_proxy.ignored{format, param}`): `top_p`, `top_k`, `stop_sequences`, `thinking`, `output_config.effort`, `context_management`, `safeguards`, `cache_control`, `service_tier`, `inference_geo`, `diagnostics`, `workspace_id`, unknown keys. Dropped silently: `metadata`. Refused `unsupported_parameter`: `tool_choice` `any` or `tool`, `output_config.format`, `mcp_servers`, `container`, a server tool type. Refused `unsupported_message`: image, document, and every non-text block. Out: `msg_<24 hex>`, `stop_reason` `tool_use` or `end_turn` (never `max_tokens`), `usage {input_tokens, output_tokens}`; a stream is `message_start`, one `ping`, blocks (a tool call is one `input_json_delta` with the whole arguments), `message_delta`, `message_stop`; a `ping` every 15 s of silence; a hold of up to 5 s for the first chunk so an early model error keeps its HTTP status; a deadline of what the run has left, or 300 s uncorrelated (suggested: the three numbers). Refusals before the model are plain JSON, also for `stream: true`.

### Admission, shared by both routes

1. Not ready: 503. 2. Parse and map; a refusal is 400 and not counted. 3. Count `chassis.model_calls{route}`. 4. Read `traceparent` (`parse_traceparent`: any version but `ff`, non-zero ids, exactly 55 characters for version `00`; the trace id only) and look the run up. 4a. A run's call that names a route the live config does not list (`ModelSpec.declared_routes`): 403 `model_route_denied`, counted `chassis.model_route_refused{route}` by the run's route, nothing held (012 H-3). 5. No run: count `chassis.model_calls_uncorrelated{route}`, log a warning, reserve on the uncorrelated cap; 429 when it does not fit, 403 `uncorrelated_refused` when the cap is `0` (see the uncorrelated cap). 6. A run with nothing left: 429, `chassis.model_calls_refused`. 7. Reserve on the run: the forwarded `max_tokens` is capped at the run's remainder (`budget.max_tokens` less spent and in-flight) and is the remainder when the workload sets none. 8. Call the port in the span `chassis.model.call`. 9. Settle on the usage seen.

**Budgets per call.** A run's concurrent calls never share tokens. 429 body: `{"error": {"type": "budget_exhausted", "code": "budget_exhausted", "retryable", "message"}}`; `retryable` is `true` only while in-flight calls hold the rest. On the chat route a streamed 429 is one SSE error frame then `[DONE]`; on the Anthropic route it is JSON. `max_tokens` bounds output only; LiteLLM's key budget is the hard cap.

**The uncorrelated cap.** `spec.limits.uncorrelated_tokens_per_minute` (20000, suggested; `0` in the `cloud` profile, CH-14; 0 refuses every uncorrelated call with 403 `uncorrelated_refused`, suggested, `retryable: false`; on the Anthropic proxy route `permission_error`), a fixed 60 s window per replica, shared by both listeners. A call's worst case is its `max_tokens`, or `min(1024, L)` when unset (suggested). Admitted only if spent plus reserved plus the worst case is at most L. A call with no usage is charged its whole reservation.

**`model_route` and the forced route.** A run's `RunRecord.model_route` is `ctx.model_route` from the moment it opens: the route of `input.data.task` through `spec.model.routes`, else `spec.model.route`. The gate replaces it with `spec.model.fallback_route` for its fallback attempt. The proxy sends every call of the run there, whatever `model` the workload named (`chassis.model_route_forced{route}`); a call that names the run's route is not forced. Only a route the config lists is forced; any other is refused at step 4a with the `model_route_denied` body below, never rewritten (012 H-3, 2026-11-13). An uncorrelated call is pinned, not refused (R6). One span `chassis.model.route` per admitted call with `route.requested`, `route`, `route.forced`.

**Model errors.** A `ModelError` with code `http_401` or `http_403` is 403 `model_route_denied` (`permission_error`, `this model route is not allowed for this service`) on both routes; counted `chassis.model_upstream_denied{route}`. On the Anthropic route: 400 `model_rejected_request` (upstream 400, 413, 422), 429 `model_rate_limited`, 503 `model_overloaded` (503, 529), 504 `model_timeout`, 502 `model_unavailable` (any other retryable), 500 `model_error`; every text is fixed. On the chat route: 502 when retryable, else 500, body `{"error": {"message", "type": "model_error", "code", "retryable"}}` with the same fixed text per code; mid-stream, one error frame then `[DONE]`.

**The service's budget (CH-16).** When the model router refuses a call for the service's budget (LiteLLM's `BudgetExceededError`: a 4xx whose body has `error.type: budget_exceeded`), the port raises `service_budget_exhausted`, not retryable, and both routes answer 429 with the fixed text `the service's model budget is exhausted` and the header `x-should-retry: false` (`rate_limit_error` on the Anthropic route; `type: model_error`, `code: service_budget_exhausted`, `retryable: false` on the chat route; mid-stream on the chat route, one error frame then `[DONE]`). Counted `chassis.model_budget_exhausted{route}`; the alert `ChassisModelBudgetExhausted` (`deploy/prometheus/rules/chassis.yaml`) fires on it. Added in [contract v9](../contracts/contract-v9.md). Every complete-call error on the chat route now carries `x-should-retry` from `retryable` (v9), as the Anthropic route always did.

**The run's time and end (CH-16).** Every port call takes `timeout_s`: for a correlated call, what the run has left of `budget.timeout_ms`; for an uncorrelated call, none, and the adapter's default (60 s per httpx operation, suggested) bounds it. Past it the adapter raises `timeout` (the Anthropic route's 504 `model_timeout`; 502 `timeout` on the chat route) and leaves the upstream response, which closes the connection. The adapter never retries: `num_retries` and `fallbacks` per route are set in the LiteLLM config, so a chassis retry would multiply them. A correlated call whose run ends first (done, cancelled, or timed out) is cut: the port call is cancelled and both routes answer 403 `run_ended` (`permission_error`, `the run this call belongs to has ended`, not retryable; `x-should-retry: false`), as `run_required` answers a call that names no run.

### `ToolPort` and `/mcp` (`ports/tool.py`, `server/tool_endpoint.py`, `adapters/mcp/server.py`)

```python
class ToolPort(Protocol):
    name: str
    def list_tools(self) -> Sequence[ToolDefinition]: ...
    async def call(self, name, arguments, *, idempotency_key: str | None = None) -> ToolResult: ...
```

`ToolDefinition {name, description, parameters (JSON Schema, default {"type": "object"}), read_only: true}`; `ToolResult {content, is_error}` (a failure the tool reports); `ToolError {code, message, retryable}`. A write tool (`read_only: false`; a gateway tool with no `readOnlyHint` is a write tool) needs `idempotency_key`: one effect per key, and a repeat returns the first result. A read-only call takes no key and may repeat.

| `ToolError` code | When | `retryable` | Public text |
| ---------------- | ---- | ----------- | ----------- |
| `unknown_tool` | No tool by that name | false | `no such tool` |
| `bad_arguments` | The arguments fail `parameters`; a bad `_meta.idempotency_key` on a write | false | `the tool arguments do not match the tool's input schema` |
| `idempotency_key_required` | A write with no key; on `/mcp`, a write or an unlisted name with no run in flight | false | `this is a write tool; call it inside a run` |
| `tool_denied` | The gateway answered 401 or 403, or its allow-list refusal text | false | `this tool is not allowed for this service` |
| `tool_unavailable` | No answer, 5xx, 408, 429 (retryable); any other unexpected status (not retryable) | true | `the tool could not be reached; retry later` |
| `guardrail_blocked` | The `tool_output` stage blocked the result | false | `a guardrail blocked this content` |
| `guardrail_unavailable` | The guardrail failed, `fail_closed` | true | `the guardrails cannot check content now; retry later` |

`/mcp` (streamable HTTP, stateless, FastMCP) lists what the port lists, with the definition's own `parameters` as the input schema and `read_only` as `readOnlyHint`. A `ToolError` is an MCP tool error result (`isError: true`) whose text is the public message and whose structured content is `{code, message, retryable}`; never a transport failure. The workload never picks the key: for a write inside a run the endpoint derives `tk1:` plus 40 hex of sha256 of `<run key>|<tool>|<canonical arguments>|<nonce>` (suggested), where the run key is the run's idempotency scope (so a replay or a gate rerun repeats the same key) and the nonce is the optional `_meta.idempotency_key`. A name the port does not list is still sent to the port as a write tool. One `chassis.tool.call` span per call (`tool`, `trace_id`, `request_id`), `chassis.tool_calls{tool}`; tool calls are named to a run, not charged to it. Tool descriptions and outputs are data, never instructions.

**The gateway adapter** (`spec.adapters.tools: mcp`, `adapters/mcp/gateway.py`): an MCP client to LiteLLM's MCP gateway with `LITELLM_MCP_URL`, `LITELLM_API_KEY`, `LITELLM_MCP_AUTH_HEADER` (`Authorization`); 30 s per call, a list refresh every 60 s (suggested). The gateway's per-key allow-list is the hard limit; a tool off the list is not listed and not called. A name the cached list lacks gets one refresh (10 s cooldown, suggested), then `unknown_tool`, or the refresh's own error if the refresh fails; no tool request is made, so the write-key and schema checks are never skipped (CH-14). Names are the gateway's `<server>-<tool>`. The key goes as `_meta.idempotency_key` and as the `idempotency_key` argument when the tool's schema declares it. No redirect, `trust_env=False`.

## 8. Security

### `AuthPort` and scopes (`ports/auth.py`, `server/security.py`)

`authenticate(token: str | None) -> Principal {subject, scopes, issuer}`. Errors: `unauthenticated` (not retryable), `auth_unavailable` (retryable). `NoAuth` answers the anonymous principal with scope `*`. Adapters: `none`, `static` (`CHASSIS_STATIC_TOKENS`, a fake, refused in `cloud`), `oidc` (`OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`; RS256 or ES256; JWKS cached 300 s; leeway 30 s, suggested).

One middleware on the public app checks every HTTP request but `/health` and `/ready`, before limits, idempotency, and the run. The token is read from `Authorization: Bearer` and removed; it never reaches the workload, a log, or a span.

| Routes | Scope (`spec.security`) |
| ------ | ----------------------- |
| `/v1/run`, the OpenAI and Anthropic interfaces (public port), `/v1/mcp` | `run_scope` (`agent:run`) |
| `/v1/feedback` | `feedback_scope` (`agent:feedback`) |
| `/manifest`, `/openapi.json`, `/docs`, `/redoc` | `read_scope` (`agent:read`) |
| `/metrics` | `spec.observability.metrics_scope`, or none |
| any other path | a valid token, no scope |

Codes: 401 `unauthenticated`, 403 `forbidden`, 503 `auth_unavailable` (`Retry-After: 1`), in the route's own body shape, counted `chassis.auth_refused{code, path_class}`. The MCP inner hop carries the caller's token and is checked again; loopback grants nothing. Caller tokens are never accepted on the proxy listeners.

### Guard stages (`ports/guardrail.py`, `server/guard.py`)

`check(text, stage) -> Verdict {allowed, text, reasons}`; `stage` is `input`, `output`, or `tool_output`. Adapters: `none` (passes everything), `fake` (`KeywordGuardrails`), `litellm` (`POST /guardrails/apply_guardrail`; names per stage from `LITELLM_GUARDRAIL_INPUT`, `_TOOL_OUTPUT`, `_OUTPUT`, `_CHUNKED`; long text chunked at 384 pieces with 32 overlap, suggested).

| Stage | Where | Text | On a block |
| ----- | ----- | ---- | ---------- |
| `input` | before the engine | `input.text` and every string in `input.data` | No run; one `error guardrail_blocked` |
| `tool_output` | `/mcp` on both proxy listeners, after the tool answered | The result's text | An MCP tool error `guardrail_blocked`; the run goes on. A masked text is the result |
| `output` | after a complete answer | The answer | The deltas, tool calls, and `end` are dropped; one `error guardrail_blocked`. A masked text replaces the deltas and `end.output.text` |

Each stage is on by default (`spec.guardrails.<stage>`). `on_unavailable: fail_closed` (default) gives `guardrail_unavailable`, retryable; `fail_open` passes the text and counts `chassis.guardrail_unavailable{stage}`. A block counts `chassis.guardrail_blocked{stage, reason}`; never the text.

**The stream secret guard** (`server/stream_guard.py`, `spec.guardrails.stream_secrets`, default true): on every streamed run, in every lane, with any adapter. A last word that could start a secret or a link is held until the next whitespace (at most 4096 characters held). The held text and the last 256 characters sent are matched against `SECRET_PATTERNS` (`sk-` keys, AWS access key ids, private key blocks, `*_API_KEY=` and like assignments, bearer tokens). A match drops the held text and ends the stream with one `error guardrail_blocked`; `end.output.text` is checked too. Counted as `chassis.guardrail_blocked{stage="output_stream", reason="secret"}`. Regex only; text flushed at a non-delta event can leave in part.

**The output URL allow-list** (`server/output_filter.py`, `spec.guardrails.output_url_allow`, default `[]`): in a complete or streamed answer, the destination of every markdown image, link, reference definition, and HTML `src=` or `href=` is emptied when its URL is absolute and its host is not listed. An entry is a host, `*.example.com`, or `*` (off). Only `http` and `https` can be allowed. Default deny. Masked after the `output` stage, so the envelope, the cache, and the result event hold the masked text. Counted as `chassis.output_url_masked`.

### Rate limit (`server/rate_limit.py`)

One token bucket per agent and per caller (`Principal.subject`) in `StatePort`, shared by every replica; key `ratelimit:<agent>:<sha256(subject)[:32]>`. Counted routes: `/v1/run`, the OpenAI and Anthropic interfaces, `/v1/feedback`; not `/v1/mcp` (its inner call counts once), reads, or probes. With auth off there is no caller, so no limit. `spec.limits.rate_limit` (true), `requests_per_minute` (600), `request_burst` (100), all reloadable. Past the limit: 429 `rate_limited`, `Retry-After: <whole seconds>`, in the route's shape, before the body is read; counted `chassis.rate_limited{path_class}`. A store outage follows `rate_limit_on_unavailable`: `fail_open` (default in `fake` and `local`; `chassis.rate_limit_unavailable`) or `fail_closed` (the `cloud` default, CH-14, P-26; 503 `state_unavailable`, `Retry-After: 1`). At most 8 compare-and-set attempts on a lost race (suggested).

### AI-generated marking (`server/marking.py`, `adapters/ai_marking`)

Marking is on when `spec.governance.risk_class` is `limited` or `high`, or `ai_generated_marking: true`; a `limited` or `high` agent with `ai_generated_marking: false` is refused at load. When on, every response on a run route (`/v1/run`, the OpenAI and Anthropic interfaces, `/v1/mcp`), complete, streamed, and refusals alike, carries `X-AI-Generated: true` and `X-AI-Disclosure-Required: true`, and `Access-Control-Expose-Headers` names both. The chassis sets them on the way out; a workload cannot remove them. The OpenAI and Anthropic bodies carry `ai_marking` (section 5); result events carry `aigenerated` (section 6). `Response` has no marking field. `/health`, `/ready`, `/manifest`, `/v1/feedback` are not marked. With marking off nothing is sent.

### `spec.trust` and the admission rule

`spec.trust` is `trusted` or `untrusted`, restart-only. Default `trusted` in `fake` and `local`; required in `cloud`. `untrusted` needs `spec.engine.connector: remote`; any other lane is refused at load. `trusted` with `remote` is allowed. It is shown as `agent.trust` in `/manifest` and is not a security control by itself: the cluster's `ValidatingAdmissionPolicy` `agent-trust-rule` is, with `agent-chassis-version` for the minimum chassis version (both rendered by the library chart's `agent-lib.admission`, installed by `deploy/helm/agent-admission`; the same policies on the PoC-5 cluster, `deploy/kind/poc05/admission/`). Their rules, in short: every pod carries `agents.platform/trust`; an `untrusted` pod never shares a pod with the chassis; outside the remote lane every image is under the platform's registry prefix and from the cluster's own store; a `trusted` sidecar workload image is in the platform-owned `trustedRepositories`; a remote or tool pod runs under `runtimeClassName: gvisor` with `automountServiceAccountToken: false` and takes only its own `<name>-token` Secret, named after its `app.kubernetes.io/name`; one chassis container per pod, with no `command` override; only the chassis references a Secret; the chassis image is at or above the minimum chassis version (rule 9, in `agent-chassis-version`: a supported tag on a tag-loaded cluster, a supported digest on a digest cluster; its binding warns for one ring cycle after a new minimum, then denies, ADR-001 item 9). The trust label is one-way: `untrusted` is always believed, `trusted` only when the platform agrees.

## 9. Observability

`TelemetryPort` (`ports/telemetry.py`): one span per call with attributes and a parent; counters. Adapters: `memory`, `otel` (the OpenTelemetry SDK, OTLP over HTTP, the standard `OTEL_EXPORTER_OTLP_*` and `OTEL_SERVICE_NAME`). The run span's trace id is the request's W3C trace id and its span id is the parent id of the workload's `traceparent`, so the workload's spans hang under it.

### Spans

| Span | Attributes |
| ---- | ---------- |
| `chassis.run` | `agent`, `interface`, `request_id`, `trace_id` (32 hex), `route` (the run's resolved route), `task` (only when `spec.model.routes` named it), `user.id`, `session.id`, `error.code` and `error.message` (redacted) on a failed run; continues the caller's `traceparent` when it names the run's trace |
| `chassis.engine.run` | `lane`, `request_id`, `a2a.task_id`, `a2a.transport_error`; plain mode adds `a2a.protocol`, `a2a.usage_known` |
| `chassis.model.call` | `route`, `trace_id`, `request_id`, `agent`, `correlated`, `format` (`openai` \| `anthropic`) |
| `chassis.model.route` | `route.requested`, `route`, `route.forced` |
| `chassis.tool.call` | `tool`, `trace_id`, `request_id` |
| `chassis.evaluator.score` | `request_id`, `trace_id`, `evaluator`, `mode`, `attempt`, `evaluator.score`, `evaluator.passed`, `evaluator.threshold`, `evaluator.latency_ms` |
| `chassis.idempotent_replay` | `interface`, `mode` |
| `chassis.feedback` | `request_id`, `feedback_id`, `trace_id` |

**Baggage.** The chassis drops a caller's `baggage` header. It builds its own from the run span: `user.id` = `sha256(Principal.subject)[:32]` in hex, the rate limit's hash (CH-14, P-27; none with auth off), `session.id` = the request header `spec.observability.session_header` names (`x-session-id`; at most 256 bytes, printable). The A2A connector sends it on every A2A request of the run, next to `traceparent`, in every lane; none when both are absent. The template server's telemetry setup (`workload_a2a.telemetry`) puts both on the workload's spans and on its model and MCP calls. Not a credential, not in `ctx`.

### Metrics (`metrics.py`, `GET /metrics`)

Declared families, every sample labeled `agent`:

| Family | Type | Labels |
| ------ | ---- | ------ |
| `chassis_requests_total` | counter | `interface` |
| `chassis_run_duration_seconds` | histogram | `interface`, `status` |
| `chassis_run_errors_total` | counter | `interface`, `code` |
| `chassis_engine_run_duration_seconds` | histogram | `lane`, `status` (the A2A call to the workload; 122 X-5's chassis-side hop timer) |
| `chassis_model_calls_total` | counter | `route` |
| `chassis_model_call_duration_seconds` | histogram | `route`, `status` |
| `chassis_model_tokens_total` | counter | `route`, `direction` (`input`, `output`) |
| `chassis_model_cost_microusd_total` | counter | `route`; only for a route in `spec.observability.prices` |
| `chassis_model_budget_exhausted_total` | counter | `route` (CH-16: the router refused for the service's budget) |
| `chassis_tool_calls_total` | counter | `tool` |
| `chassis_gate_runs_total` | counter | `mode` |
| `chassis_gate_retries_total`, `chassis_gate_fallbacks_total` | counter | |
| `chassis_feedback_total` | counter | `label` |
| `chassis_runs_in_flight` | gauge | the runs in flight on this replica, read from the run registry at scrape time (CH-15) |

Every other `TelemetryPort.counter(name, ...)` shows as `<name>_total` with `.` and other characters made `_`. Timed spans: `chassis.run`, `chassis.engine.run`, `chassis.model.call`, `chassis.tool.call`, `chassis.evaluator.score`; buckets 0.001 s to 120 s, 21 bounds, the ones below 100 ms so a millisecond hop resolves (suggested). The cost is `input_tokens * input_per_mtok + output_tokens * output_per_mtok` in whole micro-USD, an estimate, not LiteLLM's bill.

Counter names the chassis uses (labels in braces): `chassis.requests{interface}`, `chassis.requests_refused{reason}` (`trace_id_in_use`, `overloaded`), `chassis.inbound_ignored{interface, param}`, `chassis.trace_id_reminted{interface}`, `chassis.client_disconnected{interface}`, `chassis.idempotency.{claimed, refused{code}, waited, taken_over, lease_lost, fenced, renew_failed, unknown_version, not_cached{reason}, finish_failed, replayed{interface}, run_fenced{interface}}`, `chassis.config.{reloaded, rejected{reason}, poll_failed{reason}}`, `chassis.events.{published{topic}, publish_failed{topic}, publish_abandoned}`, `chassis.model_calls{route}`, `chassis.model_calls_uncorrelated{route}`, `chassis.model_calls_refused{route}`, `chassis.model_proxy.ignored{format, param}`, `chassis.model_upstream_denied{route}`, `chassis.model_budget_exhausted{route}`, `chassis.model_route_forced{route}`, `chassis.model_route_refused{route}`, `chassis.model_stream_timeout{route}`, `chassis.model_tokens{route, direction}`, `chassis.model_cost_microusd{route}`, `chassis.tool_calls{tool}`, `chassis.tool_errors{tool, code}`, `chassis.tools.refresh_failed`, `chassis.remote.auth_failed{reason}`, `chassis.remote.run_required{reason}`, `chassis.remote.run_secret_skipped{reason}`, `chassis.auth_refused{code, path_class}`, `chassis.guardrail_blocked{stage, reason}`, `chassis.guardrail_unavailable{stage}`, `chassis.output_url_masked`, `chassis.rate_limited{path_class}`, `chassis.rate_limit_unavailable`, `chassis.evaluator_calls{evaluator, mode, outcome}`, `chassis.gate_runs{mode}`, `chassis.gate_retries`, `chassis.gate_fallbacks`, `chassis.gate_failed_open`, `chassis.gate_stopped{reason}` (`timeout`, `budget`), `chassis.evaluator_dropped`, `chassis.evaluator_abandoned`, `chassis.feedback{label}`, `chassis.feedback_failed{code}`, `chassis.feedback.lookup_failed`, `chassis.on_finished_failed`. suggested: every name.

### Logs (`logs.py`)

One JSON object per line on stderr: `time` (UTC, ISO 8601, milliseconds, `Z`), `level`, `logger`, `message`, `agent`, the record's extra fields, `exception` when there is a traceback; inside a run, `request_id` and `trace_id` (32 hex). uvicorn's lines take the same format. The message, the fields, and the traceback pass `chassis.redaction`; the ids do not. The level is `spec.observability.log_level`. No key, token, client idempotency key, comment, or guarded text is ever logged.

## 10. Feedback

`POST /v1/feedback` (`server/feedback.py`), public port only, mounted when `spec.feedback.enabled` (true) and `spec.adapters.feedback` is not `none`; needs `feedback_scope`. Not on the proxy listeners.

Body, strict: `request_id` (required), `score` (required, a number in [0, 1]), `label?` (at most 64 characters), `comment?` (at most 2000), `feedback_id?`. `trace_id` and `subject` in the body are refused.

| Status | Code | When |
| ------ | ---- | ---- |
| 202 | | Written, or the same `feedback_id` seen before. Body `{"feedback_id", "trace_id"}` |
| 401, 403 | `unauthenticated`, `forbidden` | Section 8 |
| 404 | `unknown_request` | No lookup record: unknown, unfinished, expired, or another agent's run |
| 409 | `feedback_id_conflict` | The `feedback_id` was used for another request |
| 422 | | The body fails validation (FastAPI's shape) |
| 429 | `rate_limited` | Section 8 |
| 501 | `feedback_not_configured` | `NoFeedback` injected |
| 502 | `feedback_rejected` | The store refused it |
| 503 | `feedback_unavailable`, `state_unavailable`, `auth_unavailable`, `not_ready` | Retryable, `Retry-After: 1` |

**The lookup record.** After every run whose engine stream ended, `chassis:feedback:v1:req:<agent>:<sha256(request_id)>` -> `{"trace_id", "agent"}` in `StatePort` with `ttl = spec.feedback.lookup_ttl_s` (604800). Written before a complete answer goes out; a failure counts `chassis.feedback.lookup_failed`. The record holds no subject.

**Idempotency** by `feedback_id`: absent, it is 32 hex of sha256 of `request_id` and the subject, so one caller has one feedback per request. The first time an id is seen its time and request id go to `chassis:feedback:v1:id:<agent>:<sha256(feedback_id)>` (set if absent, same TTL); a repeat sends the first time again.

`FeedbackPort.submit(Feedback {feedback_id, request_id, trace_id (32 hex), score, label, comment, subject, timestamp})`, idempotent per id. Adapters: `none`, `memory`, `langfuse` (`POST /api/public/scores`, score id = `feedback_id`, trace id = the W3C trace id; `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`). The comment goes to the store as written; logs and spans never hold it.

**The event** (`spec.feedback.events: true`, default false): `agents.feedback.received.v1`, after a 202, in the background. `id` is a uuid5 of `<feedback_id>:<score>:<label>:<comment>`; `source` `/agents/<agent>`; `subject` the `request_id`; `dataschema` `feedback.v0.json`; `traceparent` with the run's trace id; `partitionkey` the sha256 hex of the `request_id`. Payload (`feedback.v0.json`): `feedback_id`, `agent`, `request_id`, `trace_id`, `score`, `label`, `comment` (redacted), `subject`, `received_at`.

## 11. The evaluator gate

`GatedEngine` (`server/evaluator_gate.py`) wraps the lane's connector when `spec.harness.evaluator_gate` is true. It refuses to start with `spec.adapters.evaluator: none`. `EvaluatorPort.score(EvalCase {input, output, reference?}) -> Score {value in [0, 1], evaluator, reason, input_tokens, output_tokens, cost_usd, latency_ms}`; adapters `none`, `fake` (`ScriptedEvaluator`), `judge` (an LLM judge through `ModelPort` on `spec.model.judge_route`, else `CHASSIS_JUDGE_ROUTE`, rubric `judge-facts-v1`, reply exactly `{"score", "reason"}`), `encoder` (TEI `POST {CHASSIS_ENCODER_URL}/predict`, the `entailment` label's score).

- **Sample.** A call is gated when the hash of `request_id` falls under `evaluator_sample_rate` (1.0; reloadable), so one id always decides the same.
- **Sync mode** (`evaluator_mode: sync`, complete calls): each attempt's events are buffered; the answer passes when `score >= threshold` (0.7). A failed answer is rerun with the same `ctx`, up to `retries` (1, at most 5) more times; a failed attempt's events never reach the client. Then one fallback attempt with `ctx.model_route = spec.model.fallback_route`, enforced by the proxy, not scored, `end.status: fallback`. With no `fallback_route` the run ends in one `error evaluator_gate_failed` and no answer. The client sees one `start`, one `metrics` per earlier attempt, one `metrics` per score (the judge's tokens, `model_route` = the judge's route, `attempt` = the attempt scored), the chosen attempt's events, one `end`. So `usage` includes the judge; the chassis numbers `attempt`. An attempt that ends in `error` goes out as is, not scored. An evaluator error fails open: the attempt goes out, `chassis.gate_failed_open`.
- **Async mode**, and every stream: the answer leaves unchanged; the score goes on a `chassis.evaluator.score` span and `chassis.evaluator_calls{evaluator, mode, outcome}` in the background (at most 64 pending; more are dropped). No retry, no fallback.
- **One budget** (017 H-4): one `budget.timeout_ms` and one `budget.max_tokens` across the attempts and the fallback; each attempt gets what is left, and the judge's tokens count against it. A rerun starts only with time and tokens left; else the run ends in one `error`: `a2a.timeout` (retryable) with no time left, `budget_exhausted` (not retryable) with no tokens left, counted as `chassis.gate_stopped{reason}` (`timeout` or `budget`).
- Known limits: a rerun reruns tool calls, deduped by the run's key.

## 12. The config

`ChassisConfig` (`server/config.py`, `chassis-config.v0.json`): `profile` (`fake` | `local` | `cloud`), `agent {name, version}`, `version?`, `spec`. suggested: every name and default. Reload: section 6.

| Block | Fields and defaults |
| ----- | ------------------- |
| `spec.adapters` | `model`, `config`, `telemetry`, `tools`, `state`, `events`, `auth`, `guardrails`, `evaluator`, `feedback`; each null means the profile default. `engine` is refused |
| `spec.engine` | `connector` `sidecar`; `handle`, `url`, `uds`, `auth {scheme bearer, token_env, previous_token_env}`, `probe_timeout_s` 2.0, `protocol` `chassis`, `a2a {usage_key null, context_id omit}` |
| `spec.model` | `route` `fake-route`; `fallback_route` null (restart-only); `routes` `{}` (task to route, [models](models.md)); `judge_route` null. `route`, `routes`, `judge_route` reload |
| `spec.prompt` | `version` null |
| `spec.trust` | `trusted` (required in `cloud`) |
| `spec.interfaces` | `openai`, `anthropic`, `mcp`: true |
| `spec.limits` | `max_tokens_max` 8000; `timeout_ms_max` 120000; `messages_max` 256; `body_bytes_max` 1048576; `uncorrelated_tokens_per_minute` 20000 (`cloud`: 0); `rate_limit` true; `requests_per_minute` 600; `request_burst` 100; `rate_limit_on_unavailable` `fail_open` (`cloud`: `fail_closed`); `max_in_flight` 32 (CH-15, per replica; reloads) |
| `spec.idempotency` | `enabled` true; `ttl_s` 86400 (at most 30 days); `lease_s` 5 (at most 3600); `wait_poll_ms` 100; `max_entry_bytes` 1048576 |
| `spec.events` | `result_events` false; `consume` null (a value is refused) |
| `spec.security` | `run_scope` `agent:run`; `feedback_scope` `agent:feedback`; `read_scope` `agent:read` |
| `spec.guardrails` | `input`, `tool_output`, `output`: true; `on_unavailable` `fail_closed`; `stream_secrets` true; `output_url_allow` `[]` |
| `spec.harness` | `evaluator_gate` false; `threshold` 0.7; `retries` 1 (0 to 5); `evaluator_sample_rate` 1.0; `evaluator_mode` `sync` |
| `spec.feedback` | `enabled` true; `lookup_ttl_s` 604800; `events` false; `score_name` `user-feedback` |
| `spec.observability` | `session_header` `x-session-id`; `log_level` `info`; `metrics_scope` null (`cloud`: `spec.security.read_scope`); `prices {}` per route `{input_per_mtok, output_per_mtok}` |
| `spec.governance` | `risk_class` `minimal`; `ai_generated_marking` null |

### Profiles and adapter names (`profiles.py`)

`spec.adapters` merges over the profile defaults per field. `cloud` refuses `fake`, `memory`, and `static` for every port; `inprocess` is allowed in `fake` and `local` only. Real adapters are built from the environment, lazily; a missing required variable is `AdapterNotAvailable` at start.

| Port | Names | `fake` | `local` | `cloud` |
| ---- | ----- | ------ | ------- | ------- |
| `model` | `fake`, `litellm` (`LITELLM_BASE_URL`, `LITELLM_API_KEY`) | `fake` | `litellm` | `litellm` |
| `config` | `memory`, `minio`, `s3` (`CONFIG_S3_ENDPOINT`, `CONFIG_S3_ACCESS_KEY`, `CONFIG_S3_SECRET_KEY`) | `memory` | `minio` | `s3` |
| `telemetry` | `memory`, `otel` | `memory` | `otel` | `otel` |
| `tools` | `fake`, `mcp` | `fake` | `mcp` | `mcp` |
| `state` | `memory`, `valkey` | `memory` | `valkey` | `valkey` |
| `events` | `none`, `memory`, `kafka`, `dapr` | `none` | `none` | `none` |
| `auth` | `none`, `static`, `oidc` | `none` | `none` | `oidc` |
| `guardrails` | `none`, `fake`, `litellm` | `none` | `none` | `litellm` |
| `evaluator` | `none`, `fake`, `judge`, `encoder` | `none` | `none` | `none` |
| `feedback` | `none`, `memory`, `langfuse` | `none` | `none` | `langfuse` |

Every credential lives in the chassis container only. No secret is in any config file, prompt, event, or log.
