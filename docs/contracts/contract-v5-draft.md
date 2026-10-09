# Contract v5 (draft)

The design for the three contract questions of PoC-6 part B: the Anthropic Messages route on the chassis model proxy (for the Claude Agent SDK), a plain-A2A mode in the `remote` connector (for third-party agents that emit no chassis events), and the freeze of the `handle` contract and the event schema (exit criterion 8). Every change is additive over [contract v4](contract-v4.md), which still holds for everything this document does not name. Status: draft, written 2026-10-09 at commit `5860257`, from the code, the [Claude CLI capture](../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-claude-cli-capture.md), and the kagent probe note (`pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-kagent-probe.md`, merged on the integration branch, not on this one yet). Nothing here is built. The plan is [the PoC-6 work plan](../plans/2026-10-09-poc-06-bake-off.md), tasks B1 and B6. The planning doc is [PoC-6](../planning/poc/006-PoC-6-framework-bake-off.md).

Contract v5 is a version of this document, not of the wire. The event schema stays `schema_version: "0"` (see part C). When the work is built, this file is renamed `contract-v5.md` and rewritten from the code, as v4 was.

## Decisions at a glance

| | Decision | Contract it touches | `inprocess` | `sidecar` | `remote` |
| - | -------- | ------------------- | ----------- | --------- | -------- |
| A | `POST /v1/messages` on both model proxy listeners, mapped onto `ModelPort` | The workload's model contract (a second wire format on the proxy port); the remote listener's route list and refusal bodies. Not the envelope, events, `handle`, a port, the A2A mapping, or `spec.*` | Route exists, nobody calls it | Route exists on loopback, nobody calls it | The Claude Agent SDK's path. Same hop as `/v1/chat/completions` |
| B | `spec.engine.protocol: a2a` in the `remote` connector | The A2A mapping (a second, opt-in reading side) and `spec.engine.*`. Not the event schema | Refused | Refused | Opt-in per remote. No new hop |
| C | Freeze `schema_version: "0"` as the stable line, with a written rule for what is breaking | All of them, in content: none changes | None | None | None |

None of the three touches `chassis.core`. The check is `git diff --stat main -- packages/chassis/src/chassis/core packages/chassis/src/chassis/ports packages/chassis/schemas/events.v0.json` printing nothing at the end of PoC-6.

## What did not change

- **The event schema.** `events.v0.json` is unchanged. `SUPPORTED_SCHEMA_VERSIONS` is `("0",)`.
- **The envelope, `handle`, and the A2A mapping in chassis mode.** `adapters/a2a/mapping.py` is unchanged and stays byte for byte equal in `chassis` and `workload-a2a`.
- **`/v1/chat/completions` on the model proxy.** Same body, same frames, with one exception: an upstream 401 or 403 now answers 403, not 500 (A.9).
- **The Anthropic interface (public port).** `POST /v1/messages` on the public port calls the agent. It is `AnthropicInbound` and is untouched. This document is about the other route of that path, on the proxy port. The words used below are "the Anthropic interface (public port)" and "the Anthropic proxy route (proxy port)". Never the bare path.
- **Ports, `spec.adapters`, idempotency, `/ready`, shutdown, the uncorrelated cap's rule, `RequireRun`, `BearerAuth`.** As in v4.

---

## A. The Anthropic proxy route

### A.1 Decision

Add `POST /v1/messages` to the model proxy router. Both proxy listeners get it, because both mount that one router: the loopback listener and the remote listener. A workload that speaks the Anthropic Messages API, in practice the Claude Agent SDK's CLI, points `ANTHROPIC_BASE_URL` at the chassis and calls a model route through `ModelPort`, as a workload that speaks the OpenAI chat format does today.

The request is read as raw JSON and mapped by hand onto `ModelMessage`, `ToolSpec`, and `max_tokens`. The answer is built from `ModelResult` and `ModelChunk` and shaped as Anthropic's types. The route shares one admission step with the chat route: the same run lookup, the same budget reservation, the same uncorrelated cap, and on the remote listener the same `BearerAuth` and `RequireRun`. The pure mapping lives in `chassis.adapters.anthropic_compat`, next to the PoC-3 code. The route lives in `chassis.server`. Nothing in `chassis.core` or `chassis.ports` changes.

### A.2 Reasons

- The capture shows the CLI calls one path only: `POST /v1/messages?beta=true`, always with `stream: true`. It never calls `count_tokens`, `/v1/models`, or a second model. One route is enough.
- The CLI sends things strict Anthropic refuses and the SDK types cannot hold: `role: "system"` inside `messages`, a `system` list with a billing marker, unknown top-level keys such as `safeguards`. So the request cannot be validated through `MessageCreateParams` (the public interface does that and would answer 400 or 422). A hand-written reader is required.
- The model proxy already owns the three things that must not be copied: the run budget, the uncorrelated cap, and the per-listener auth. A second route that shares the admission step cannot drift from the first.
- A remote spends tokens only inside a run (ADR-005 decision 2). A route outside `RequireRun` would break that. Mounting it on the same router keeps the rule.

### A.3 Rejected

| Option | Why not |
| ------ | ------- |
| Call `AnthropicInbound` (the PoC-3 mapping) | It maps onto the canonical `Request` and calls the agent. This route calls the model, not the agent. It refuses `tools`, `tool_use`, and `tool_result`, which are the point here |
| Validate with `MessageCreateParams` | Rejects `role: system` in `messages`, drops unknown keys, and lazy iterators raise at read time. See A.2 |
| Let the SDK talk to LiteLLM directly | The remote would need a LiteLLM key. ADR-001 hard requirement 1 forbids it |
| A translating shim inside the remote's pod | Hides the credential rule in a second process and adds a hop. The CLI would still need the shim to be trusted |
| Serve `/v1/messages/count_tokens` | The CLI never called it. `ModelPort` has no tokenizer, so any answer would be a guess. It stays a 404 |
| Mount the route on the remote listener only | The router is shared by construction. A trusted sidecar workload that uses the Anthropic SDK gets it for free, and the uncorrelated cap covers it |
| Accept `x-api-key` as a credential on the remote listener | Widens the auth surface and invites two-credential ambiguity. The engine sets `ANTHROPIC_AUTH_TOKEN`, which the CLI sends as `Authorization: Bearer` |

### A.4 Request: URL and headers

- **Path.** `POST /v1/messages`. The query string is ignored: `?beta=true` and any other query are accepted and not read. No other method. A `GET` is FastAPI's 405.
- **Not served.** `/v1/messages/count_tokens`, `/v1/models`, and every other path stay 404 on both listeners. On the remote listener the 404 comes after both checks, as in v4. The 404 of `/v1/messages/count_tokens` is in the Anthropic error shape (`not_found_error`, with `request-id`). Its 401 on the remote listener is v4's body, because the middlewares test the exact path `/v1/messages` (A.8). `/v1/models` keeps FastAPI's 404 body.
- **Body.** A JSON object. Invalid JSON (deep nesting included), or a body that is not an object, is 400 `invalid_body`. The route reads the body itself, so FastAPI never answers 422.
- **Body cap.** 4 MiB (`BODY_CAP_BYTES`, suggested). The route counts the bytes while it reads, and a declared `content-length` over the cap is refused at once, both before any parsing. A larger body is 413 `request_too_large` (code `body_too_large`, `x-should-retry: false`).

| Header | Rule |
| ------ | ---- |
| `authorization` | Remote listener: `BearerAuth` as in v4 (`Bearer <remote token>`). The header is removed before the route. Loopback listener: not checked. Never forwarded, never logged |
| `x-api-key` | Never read, never logged, never forwarded. On the remote listener a call with `x-api-key` and no `Authorization` is 401 `remote_unauthenticated`. A call with both is judged on `Authorization` alone |
| `anthropic-version` | Not read, not required, not echoed. Any value or none |
| `anthropic-beta` | Not read, not forwarded, not counted. The reply uses no beta shape, whatever the list says |
| `traceparent` | The correlation key, as on the chat route: it names the run. On the remote listener `RequireRun` needs it (403 otherwise). The CLI sends it from `ANTHROPIC_CUSTOM_HEADERS`, fixed at process start, so the remote must start one CLI process per run. A CLI reused across runs would carry a stale `traceparent` and get 403 `run_required` |
| `x-claude-code-session-id`, `user-agent`, `x-app`, `x-stainless-*`, `anthropic-dangerous-direct-browser-access` | Not read. They carry a session id and a device fingerprint, and nothing the route needs |
| `accept`, `accept-encoding` | Not read. The CLI sends `accept: application/json` for a stream. The reply is `text/event-stream` when `stream` is true, whatever `accept` says, and is never compressed |

### A.5 Request: the body, field by field

Legend. **Map**: carried onto the model port. **Drop**: accepted, not carried, counted as `chassis.model_proxy.ignored{format="anthropic", param}` (suggested: the counter name; `param` is from the fixed list below, or `other`). **Drop, silent**: not carried, not counted, not logged (it may identify a person). **Refuse**: 400 before the model is called and before `chassis.model_calls` is counted. A refusal body is in A.8. A refusal names the field by path and says why in fixed words. It never repeats a value from the request (a role, a block type, an id), so a refusal cannot echo a secret.

| Field | What the CLI sends | Decision |
| ----- | ------------------ | -------- |
| `model` | `big-default` (`ANTHROPIC_MODEL` verbatim) | **Map.** It is the route, passed as `route` unchanged, as on the chat route. Missing or not a non-empty string, or longer than 256 characters (suggested): refuse `invalid_body`, before admission, so a huge value reaches no counter label, log, or span. The engine must set `ANTHROPIC_MODEL` and `ANTHROPIC_SMALL_FAST_MODEL` to a route the key lists, or the CLI sends its own `claude-*` names and LiteLLM refuses them (A.9) |
| `messages` | A list of turns, text and block content, and `role: "system"` entries | **Map.** Rules in A.5.1. Missing, empty, or not a list: refuse `invalid_body`. Over `spec.limits.messages_max`: not enforced here (the proxy has no such limit today) |
| `system` | A list of 2 text blocks: a billing marker and the agent line | **Map.** A string or a list of text blocks, joined with `"\n"` into one `ModelMessage(role="system")` at position 0, when not empty (an empty string or empty blocks give no message). A block whose text starts with `x-anthropic-billing-header:` is **dropped** first (param `system.billing_header`). Why: it is a note for Anthropic's billing, it changes per run, and it breaks a provider's prefix cache. A non-text block in `system`: refuse |
| `tools` | 20 to 24 entries, each `{name, description, input_schema}` | **Map.** Rules in A.5.2 |
| `tool_choice` | Not sent | **Map** `auto` or absent: nothing. `none`: send no tools to the model (history keeps its tool blocks). `any`, `tool`, or any other value: **refuse** `unsupported_parameter`, since the port cannot force a tool. `disable_parallel_tool_use` inside `auto` or `none`: **drop** (param `tool_choice.disable_parallel_tool_use`) |
| `max_tokens` | `32000` | **Map.** Required, an integer of at least 1, else refuse `invalid_body`. A JSON boolean is refused (it is an integer in Python, not in JSON). It goes to admission as the wanted tokens. A correlated call is capped at what its run has left, so 32000 is clamped to the run's budget. An uncorrelated call reserves it whole, so 32000 is above the default cap of 20000 and is refused with 429 (A.8). That is the existing rule, not a new one |
| `stream` | `true` always | **Map.** A boolean, default `false`, else refuse `invalid_body`. `true` is the SSE answer (A.7.1), `false` the JSON message (A.7.2) |
| `temperature` | Not sent | **Map.** A number from 0 to 1 is passed through; anything else, a JSON boolean included, is refused `invalid_body`. Absent: the chat route's default, 0.0 (suggested) |
| `top_p`, `top_k`, `stop_sequences` | Not sent | **Drop.** The port carries none of them |
| `thinking` | `{"type":"adaptive","display":"updates"}` | **Drop** (param `thinking`). The reply never has a thinking block |
| `output_config` | `{"effort":"high"}` | `effort`: **drop** (param `output_config.effort`). `format`: **refuse** `unsupported_parameter` (structured output is not on the port). Not an object: refuse `invalid_body` |
| `context_management` | `{"edits":[{"type":"clear_thinking_20251015",...}]}` | **Drop.** A provider-only beta |
| `metadata` | `{"user_id": "<JSON with device_id, account_uuid, session_id>"}` | **Drop, silent.** Never read for the session id either; correlation is the `traceparent` |
| `safeguards` | First call of a run only: classifier context with the cwd, HOME, and rule roots | **Drop** (param `safeguards`, by name only; the value is never read or logged, since it holds paths). The CLI continues locally when no verdict comes back |
| `cache_control` | In `system[1]` and the last `system`-role block; 2 per call | **Drop** (param `cache_control`, counted once per call, wherever it appears: `system`, a message block, a tool). Never refused |
| `service_tier`, `inference_geo`, `diagnostics`, `workspace_id` | Not sent | **Drop** (param is the name) |
| `mcp_servers`, `container` | Not sent | **Refuse** `unsupported_parameter`, as the public interface does |
| any other top-level key | `safeguards` was one in CLI 2.1.294 | **Drop** (param `other`). Anthropic adds fields often. A field that changes meaning is found by the counter, as in ADR-003 item 1 |

Counters count once per call per `param`, not once per occurrence.

#### A.5.1 Messages

Each Anthropic turn becomes one or more `ModelMessage`s, in order.

| Anthropic | Becomes |
| --------- | ------- |
| `content` is a string | The text, as is |
| `content` is a list | Blocks in A.5.1's table below. `text` blocks join with `"\n"`, the model proxy's rule |
| `role: "user"` | A turn with no content (no text and no `tool_result`, after `thinking` blocks are dropped) is refused `invalid_body` at `messages[i].content`: `a user turn needs content`. Otherwise a `user` message with the joined text. If the turn has `tool_result` blocks, each becomes a `tool` message first (below), then the text, when not empty, as one `user` message |
| `role: "assistant"` | One `assistant` message, or none: a turn that is empty after its `thinking` blocks are dropped is skipped. Otherwise one message. `content` is the joined text, or `None` when there is none and there are `tool_use` blocks. `tool_use {id, name, input}` becomes `ToolCallRequest(call_id=id, name=name, arguments=input)`, in order. `input` must be an object, else refuse `unsupported_message` at `messages[i].content[j].input` |
| `role: "system"` (the `mid-conversation-system` beta) | An entry with no text is skipped. Otherwise one `system` message **in place**, text blocks joined. It is not merged into the top message and not moved. The CLI sends an `# Environment` block and, after each tool result, a `<total_tokens>` note, which can be the last message. (suggested: in place. Qwen3's chat template renders a later `system` as a normal turn, and LiteLLM hoists it for Anthropic models. If PoC-6c finds a route whose template refuses it, add a fold rule then) |
| `role` is anything else | Refuse `unsupported_message` at `messages[i].role` |
| The last message is `assistant` | Refuse `unsupported_message`: assistant prefill is not supported (as the public interface). A last message with role `system` or `user` is fine |

| Block | Rule |
| ----- | ---- |
| `text` | Carried. `cache_control` dropped. `citations` dropped silently |
| `tool_use` | Assistant only (see above). On a `user` turn: refuse |
| `tool_result {tool_use_id, content, is_error}` | `user` turns only. Becomes `ModelMessage(role="tool", tool_call_id=tool_use_id, content=<text>)`. `content` is a string, or a list of `text` blocks joined with `"\n"`, or absent (empty string). An `image`, `document`, or other block inside: refuse. `is_error` is dropped (param `tool_result.is_error`): the chat format has no error flag, and the CLI writes the error text into `content`. The id resolves to the nearest preceding `tool_use` with that id (an id may repeat across turns). With no such `tool_use` before it, refuse `unsupported_message` (`tool_result: no earlier tool_use has this id`). The text never repeats the id. All `tool_result` blocks must come before any text in the turn, as Anthropic requires, else refuse |
| `thinking`, `redacted_thinking` | **Drop** (param `content.thinking`). This reply never carries one, so one can only arrive from a resumed session. The model does not need its own reasoning back |
| `image`, `document`, `search_result`, `server_tool_use`, `web_search_tool_result`, `mcp_tool_use`, `mcp_tool_result`, `container_upload`, `tool_reference`, and any block with another `type` | Refuse `unsupported_message` at `messages[i].content[j]`: `block type '<type>' is not supported`. The port is text-only. (`ToolSearch` is off for a non-first-party base URL, so `tool_reference` should not appear) |

A `tool_use` that has no `tool_result` in the next turn is not checked here. The upstream refuses it, and the answer is the model error of A.9.

#### A.5.2 Tools

`tools[i]` becomes `ToolSpec(name, description, parameters)`.

- `name` and `description` carry over. A missing `description` is `""`. A missing or non-string `name`: refuse.
- `parameters` is `input_schema` with the top-level key `$schema` removed (suggested: top level only; a nested one stays). A missing `input_schema` is `{"type":"object"}`. Why: the CLI's built-in tools carry `$schema` (JSON Schema 2020-12), and some providers refuse it.
- `type` must be absent or `custom`. Any other value (`bash_20250124`, `web_search_20250305`, `computer_...`, a server tool) is refused `unsupported_parameter` at `tools[i].type`.
- `cache_control`, `defer_loading`, `strict`, `eager_input_streaming`, `allowed_callers`, `input_examples`: dropped (param `tools.<field>`, from a fixed list; others `other`).
- The MCP tool the capture saw is named `mcp__glossary__glossary_lookup`. The names are opaque to the chassis. The CLI runs its own MCP client against the chassis `/mcp` on the same listener, with the remote token and the `traceparent` in the server's `headers`. The chassis does not map them.
- The body is 57 to 61 KB per call, mostly tools. That is about 15k tokens. It matters for an SLM's context in PoC-6c, not for the route. Narrow the tools with the CLI's `allowed_tools`.

### A.6 Admission: what it shares with the chat route

The chat route's steps move into one function in `server/model_proxy.py`, used by both routes. The order is fixed:

1. Not ready: 503.
2. Parse and map the body. A refusal is 400, and the call is **not** counted.
3. Count `chassis.model_calls{route}`.
4. Read `traceparent`. Look up the run in `app.state.runs`.
5. No run: count `chassis.model_calls_uncorrelated`, log the warning, reserve on `app.state.uncorrelated_cap`. Refuse with 429 if it does not fit.
6. A run with nothing left: count `chassis.model_calls_refused`, 429.
7. Otherwise reserve on the run (`record.reserve`), so concurrent calls of one run never get the same tokens. The forwarded `max_tokens` is the reservation.
8. Call `ModelPort.complete` or `.stream` inside the span `chassis.model.call` (gains the attribute `format`: `openai` or `anthropic`; suggested).
9. Settle the reservation on the usage seen, as `_Hold` does. A stream the client leaves is charged on this route when the model call started: its last known usage, or its whole reservation when usage is unknown (suggested), for a correlated and an uncorrelated stream alike. A stream whose model call never started is charged nothing, and so is a call where a chassis bug (not a `ModelError`) stops it before the first chunk. The route does not read `receive` during the hold, so a client that leaves inside the hold window is seen only at the first send, after the window; no test can observe a leave inside it. The chat route keeps its rule, where a correlated stream the client leaves is not charged (Known gap 004 G-2); that is not changed here.

What follows from sharing:

- **One cap, one budget.** A run that spends tokens on both routes spends one budget. The uncorrelated cap is `app.state.uncorrelated_cap`, created once by `model_proxy_router`; the Anthropic route reads it and never makes a second one.
- **The remote listener** needs no new wiring. `create_remote_proxy_app` includes `model_proxy_router`, so `/v1/messages` is behind `BearerAuth` then `RequireRun`, and the route list becomes `POST /v1/chat/completions`, `POST /v1/messages`, `/mcp`. Nothing else. A websocket is still closed with 1008.
- **The two refusals** from the middlewares (401 and 403) answer in the Anthropic shape on the path `/v1/messages` and in the v4 shape everywhere else (A.8).
- **Credentials.** The route never forwards a header and adds none. The model adapter holds the key.

### A.7 Response

The model's answer is read from `ModelResult` (complete) or `ModelChunk`s (stream). `ModelChunk` carries a whole tool call (`ToolCallRequest` with parsed `arguments`), not fragments, so the route never assembles partial JSON.

Ids: `msg_<24 hex>` (suggested). The same 24 hex, as `req_<24 hex>`, is the `request-id` response header on every answer, success or error. `model` in the message is the request's `model`. The answer carries `cache-control: no-cache` when it streams.

**Stop reason.** `tool_use` if the answer has at least one tool call, else `end_turn`. `max_tokens` is never sent: `ModelChunk` and `ModelResult` carry no finish reason, so a cut-off answer reads as `end_turn`. That is a known gap, and the fix is a port change, so it is not part of this work (see "Open points").

**Usage.** `input_tokens` and `output_tokens` only, from the last usage the port gave. Zeros when it gave none. No `cache_*` fields: the capture showed the CLI does not need them.

#### A.7.1 Stream (`stream: true`)

Content type `text/event-stream`. Each frame is `event: <name>\ndata: <compact JSON>\n\n`. This is the shape the CLI accepted in the capture (`notes/capture/capture_server.py`), which is the golden file for the tests.

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_...","type":"message","role":"assistant","model":"big-default","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":1}}}

event: ping
data: {"type":"ping"}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Plain "}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"call_1","name":"mcp__glossary__glossary_lookup","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"term\":\"SLM\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"input_tokens":812,"output_tokens":31}}

event: message_stop
data: {"type":"message_stop"}
```

Rules:

- **Order.** `message_start`, then `ping` once, then blocks, then `message_delta`, then `message_stop`.
- **Text.** The first non-empty `ModelChunk.text` opens a text block (`content_block_start`, `text: ""`). Each later non-empty text is one `text_delta`. An empty text is skipped.
- **A tool call** closes the open text block, then opens a `tool_use` block at the next index with `input: {}`, sends the whole arguments as one `input_json_delta` (`partial_json` is the compact JSON of the object), and closes. The block's `id` is the call id and `name` is the name. Text that comes after a tool call opens a new text block at the next index. Indexes are 0, 1, 2 and never repeat.
- **End.** The open block is closed. `message_delta` carries `stop_reason` and the usage (`input_tokens` and `output_tokens`, the run's real numbers). `message_start` carries `input_tokens: 0` because the port gives usage only at the end.
- **No content at all.** `message_start`, `ping`, `message_delta`, `message_stop`.
- **Keep-alive.** After the stream starts, a `ping` frame is sent when no chunk has come for `PING_INTERVAL_S` (15 s, suggested). The wait uses one pending read that is never cancelled, so a chunk is never lost. Why: the adapter buffers a streamed tool call until the model finishes it, so a long `Write` call is silent for a long time.
- **Hold rule.** Before sending anything, the route waits up to `FIRST_CHUNK_WAIT_S` (5 s, suggested) for the first chunk or for the model's error. If the model fails in that window, the answer is the HTTP error of A.8, with its real status, because the CLI retries on status. If the first chunk comes, or the window passes with nothing, the route sends 200 and `message_start`. An error after that is one `event: error` frame, then the stream ends with no `message_stop`:
  `event: error` / `data: {"type":"error","error":{"type":"api_error","message":"<code>: <text>"}}`.
  The model call starts when the hold starts, so the hold sets `started` on the reservation at once. A client that leaves during the hold gives the same charge as one that leaves during the stream.
- **Deadline.** The stream has a wall clock: what is left of the run's `budget.timeout_ms` when the call is correlated, else `UNCORRELATED_STREAM_DEADLINE_S` (300 s, suggested). At expiry the route sends one `event: error` frame (`timeout_error`, `model_timeout`, fixed text), closes the upstream stream, and ends with no `message_stop`. The reservation is charged whole, since the upstream may have produced tokens unseen. A stream the client leaves also closes the upstream stream at once.
- **Refusals before the model** (400, 401, 403, 429, 503) are plain JSON responses with their status, never an SSE body, also when `stream` is true. This differs from the chat route, which sends a 429 as an SSE frame.

#### A.7.2 Complete (`stream: false`)

A JSON message, status 200:

```json
{"id":"msg_...","type":"message","role":"assistant","model":"big-default",
 "content":[{"type":"text","text":"..."},
            {"type":"tool_use","id":"call_1","name":"...","input":{"term":"SLM"}}],
 "stop_reason":"tool_use","stop_sequence":null,
 "usage":{"input_tokens":812,"output_tokens":31}}
```

`content` is the text block first (omitted when the text is empty), then one `tool_use` per call. No text and no calls: `[]`. The CLI never asks for this form; the capture checked it with a self-test only.

### A.8 Errors

Every error is `{"type":"error","error":{"type":T,"message":M},"request_id":"req_..."}` with the `request-id` header. The `message` is `<code>: <text>`, as the public interface writes it, so the chassis code is not lost in Anthropic's closed set of types. Every error carries `x-should-retry: true|false`. The SDK and the CLI's SDK layer retry 408, 409, 429, and 5xx unless it says `false`. (Whether the CLI's own wrapper honors the header is untested; see "Open points".)

| Status | `error.type` | Code | When | Text | `x-should-retry` |
| ------ | ------------ | ---- | ---- | ---- | ---------------- |
| 413 | `request_too_large` | `body_too_large` | The body is over 4 MiB | `the request body is over the size limit` | false |
| 400 | `invalid_request_error` | `invalid_body` | Not JSON, not an object, a field of the wrong type, `max_tokens` missing or below 1 | Names the field: `max_tokens: a positive integer is required` | false |
| 400 | `invalid_request_error` | `unsupported_parameter` | `tool_choice` any or tool, `output_config.format`, `mcp_servers`, `container`, a tool `type` | Names the field | false |
| 400 | `invalid_request_error` | `unsupported_message` | A block, role, or id the port cannot carry (A.5.1) | Names `messages[i].content[j]` | false |
| 400 | `invalid_request_error` | `model_rejected_request` | The model route answered 400, 413, or 422. The remote is untrusted, so no upstream text reaches it. The chassis logs the upstream text (already redacted by the adapter, at most 300 characters) in one warning | `the model route rejected the request` | false |
| 401 | `authentication_error` | `remote_unauthenticated` | Remote listener, `BearerAuth` | `missing or invalid bearer token`. Sends `www-authenticate: Bearer` | false |
| 403 | `permission_error` | `run_required` | Remote listener, `RequireRun` | `the traceparent names no run in flight` | false |
| 403 | `permission_error` | `model_route_denied` | The model route answered 401 or 403 (A.9) | `this model route is not allowed for this service` | false |
| 429 | `rate_limit_error` | `budget_exhausted` | A run with nothing left, or the uncorrelated cap. The existing message text, with counts | As in v4 | `retryable` of the cap or run (true only when in-flight calls hold the rest) |
| 429 | `rate_limit_error` | `model_rate_limited` | The model route answered 429 | `the model route is rate limited; retry later` | true |
| 503 | `overloaded_error` | `not_ready` | The proxy has no ports yet | `the chassis is not ready` | true |
| 503 | `overloaded_error` | `model_overloaded` | The model route answered 503 or 529 | `the model route is overloaded; retry later` | true |
| 504 | `timeout_error` | `model_timeout` | `ModelError` code `timeout` | `the model route did not answer in time` | true |
| 502 | `api_error` | `model_unavailable` | Any other retryable `ModelError` (5xx, `connect_error`, `transport_error`) | `the model route could not be reached; retry later` | true |
| 500 | `api_error` | `model_error` | Any other `ModelError` (`bad_response`, an unmapped 4xx) | `the model call failed` | false |

- **Fixed texts.** Every model error, `model_rejected_request` included, sends the fixed text above, never `ModelError.message`. The remote is untrusted code, so no upstream body reaches it. (The chat route still sends `exc.message` today. Aligning it is a separate change, not made here.)
- **Mid-stream** an error is the `event: error` frame of A.7.1, with the same `type` and `message` and no status.
- **Telemetry.** A 403 `model_route_denied` counts `chassis.model_upstream_denied{route}` (suggested) and logs one warning with the upstream code. A chassis key that is itself invalid also answers 401 upstream, so the remote cannot tell the two apart. The operator can, from the counter and the log.
- **The middleware bodies.** `BearerAuth` and `RequireRun` choose the body by the raw request path before routing. For `/v1/messages` it is the Anthropic body above, fixed bytes except `request_id`; for every other path it is v4's `{"error":{code,type,message}}`. Status, headers, counters, and order do not change.

### A.9 A refused model route (closes the v4 open question)

v4's open question: LiteLLM refuses a route the key does not list, and the adapter raises `ModelError("http_401")`, which the proxy sends as HTTP 500. A 500 says the chassis broke. Here the caller asked for something it may not have.

**Decision.** On both model proxy routes, `ModelError` code `http_401` or `http_403` answers **403**. On the Anthropic route the body is the `model_route_denied` row of A.8. On the chat route it is `{"error":{"message":"this model route is not allowed for this service","type":"permission_error","code":"model_route_denied","retryable":false}}`. The 500 stays for real upstream failures. On the Anthropic route a stream gets the 403 as a status when the refusal comes inside the first-chunk window (A.7.1). On the chat route a stream keeps today's behavior, a 200 and an error frame, since it has no hold rule. Only the non-stream chat call changes status.

**Why.** It adds a code and changes one status, on the proxy listeners only. No event schema, envelope, or port changes. The remote gets a status it can act on, and one that does not read as an outage.

**Cost.** `pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py::test_h29_a_route_outside_the_key_is_refused_and_an_in_scope_call_is_200` asserts `500` and `http_401`, and the change fails it on purpose. The same commit updates it to `403` and `model_route_denied`. The chat route's v4 behavior is the one existing behavior this document changes.

### A.10 Files

Touched:

| File | Change |
| ---- | ------ |
| `packages/chassis/src/chassis/adapters/anthropic_compat/model_wire.py` (new) | Pure, no I/O. `parse_messages_request(raw) -> ParsedMessages` (route, `list[ModelMessage]`, `list[ToolSpec] | None`, `max_tokens`, `temperature`, `stream`, `ignored: list[str]`); `RefusedField(code, param, message)`; `message_json(result, route, msg_id)`; `StreamEncoder` (`start`, `ping`, `chunk`, `finish`, `error`) that owns the block indexes. Imports `chassis.ports.model` and `chassis.adapters.openai_compat.messages.is_empty`, and the SDK types through `types.py` |
| `packages/chassis/src/chassis/adapters/anthropic_compat/types.py` | Re-export `ToolUseBlock`, `InputJSONDelta`, `RawMessageStreamEvent` (already), so the server never imports `anthropic` |
| `packages/chassis/src/chassis/server/model_proxy.py` | Move steps 1 and 3 to 7 of A.6 into `admit(...)`, returning an admission or a refusal (status, code, message, retryable). `chat_completions` calls it and formats as before. `_HeldStream` takes response headers (`request-id`, `cache-control`) and `_Hold` gains `charge_abandoned`. Neither takes a request header. `model_proxy_router` also registers the Anthropic route. Map `http_401` and `http_403` to 403 (A.9). The module docstring names both routes |
| `packages/chassis/src/chassis/server/model_proxy_messages.py` (new) | `add_messages_route(router, app, admit)`: reads the body, calls `parse_messages_request`, `admit`, drives the stream with the hold rule and pings, formats errors by A.8 |
| `packages/chassis/src/chassis/server/remote_auth.py` | The two fixed bodies get an Anthropic variant, chosen by `scope["path"] == "/v1/messages"` |
| `pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py` | H29 expects 403 (A.9) |
| Docs: `docs/guides/chassis-reference.md`, `packages/chassis/CLAUDE.md` (the route-list sentence), `packages/chassis/src/chassis/server/proxy_app.py` and `remote_auth.py` docstrings | Name the new route and the "two `/v1/messages`" wording |

Not touched: anything under `packages/chassis/src/chassis/core/`, anything under `ports/`, `proxy_app.py` and `create_remote_proxy_app` code (they include the router already), `cli.py`, `profiles.py`, `adapters/litellm/`, `server/interfaces/anthropic.py`, `AnthropicInbound`.

### A.11 Tests

Unit, no server (`packages/chassis/tests/test_anthropic_model_wire.py`, new). One parametrized case per row of A.5 and A.5.1, each named by the field, with the exact `ModelMessage` or the exact refusal (`code`, `param`):

- `system` string, `system` list, the billing block dropped, a non-text `system` block refused.
- `role: system` in place, first, middle, and last; a last `assistant` refused; a bad role refused.
- A user turn with two text blocks (joined); `tool_result` first then text; text then `tool_result` refused; `tool_result` string, text blocks, an image block (refused), no content, `is_error`; an unknown `tool_use_id` refused.
- An assistant turn with text and `tool_use`; with `tool_use` only (`content` is `None`); `input` not an object refused; a `tool_use` in a user turn refused.
- `thinking` blocks dropped; `image` and each other block type refused; `cache_control` on a system block, a message block, and a tool (one count).
- `tools`: `$schema` removed at the top and kept when nested; missing `input_schema`; a server tool `type` refused; the dropped tool fields.
- `tool_choice`: `auto`, `none` (no tools sent), `any` and `tool` (refused), `disable_parallel_tool_use` dropped.
- `thinking`, `output_config.effort`, `context_management`, `safeguards` dropped and counted by name; `metadata` dropped and **not** counted or logged (assert with a log and counter spy); `output_config.format`, `mcp_servers`, `container` refused; an unknown top-level key counted as `other`.
- `model`, `max_tokens` (missing, 0, `true`, a string), `stream`, `temperature` (in range, out of range).
- A fixture with every field of the capture table in one body, built from the table, maps and ends with the exact messages. The raw capture logs are not committed, so the fixture is written by hand from the table.

Route, with a `ScriptedModel` (`packages/chassis/tests/test_model_proxy_messages.py`, new):

- **Streaming with a tool round-trip, `stream` true and false** (the pattern of `test_a_tool_loop_reaches_the_port_intact`). Turn 1: the model asks for a tool; assert the frames in order (A.7.1), `stop_reason` `tool_use`, the `tool_use` block's `id`, `name`, and `partial_json`. Turn 2: the client posts the assistant `tool_use` and a user `tool_result`; assert `ScriptedModel.calls` holds the assistant message with `tool_calls` and the `tool` message with the same `tool_call_id`; the answer ends `end_turn`. Every frame validates against `anthropic.types.RawMessageStreamEvent` and the complete body against `anthropic.types.Message`. A golden file holds the frames the capture server sent.
- **Admission parity.** A correlated call is charged to its run. The `max_tokens` forwarded is capped at the remainder. 429 `budget_exhausted` in the Anthropic shape with `x-should-retry`, complete and stream (a JSON body in both). The uncorrelated cap is shared: spend through the chat route, get refused on the Anthropic route, and the reverse. A concurrent pair on both routes is never given the same tokens.
- **The hold rule.** A model error before the first chunk is its HTTP status. A first chunk then an error is a 200 and an `event: error`. A first chunk later than the window is a 200 with `message_start` and `ping`. A silent gap longer than the interval gets a `ping` and loses no chunk.
- **Client leaves** during the hold and during the stream: the reservation is settled as the chat route's tests require.
- **Every row of the A.8 table**, from a `ScriptedModel` that raises the matching `ModelError`: status, `type`, `code` in the message, `x-should-retry`, and a fixed text that holds no part of `ModelError.message`.
- **Headers.** `anthropic-version`, `anthropic-beta`, `x-api-key`, `authorization` never reach the model adapter (assert the adapter's received arguments and the fake model server's headers); `?beta=true` and any other query are accepted; `accept: application/json` with `stream: true` still gets `text/event-stream`.
- **The span and counters.** `chassis.model.call` with `format=anthropic`; `chassis.model_calls` counted after a refusal does not count.

The remote listener (extend `packages/chassis/tests/test_remote_auth.py`):

- **The route-list test.** The set of route paths of `create_remote_proxy_app` is exactly `{"/v1/chat/completions", "/v1/messages", "/mcp"}`. Today's test only checks that `/mcp` is there and `/dapr` is not; the new one pins the whole set, so a route added later fails it.
- `POST /v1/messages`: no token is 401 and `x-api-key` alone is 401, both in the Anthropic body; a good token outside a run is 403 `run_required` in the Anthropic body; inside a run it is 200 and charged to the run; the same request on `/v1/chat/completions` still gets v4's body (assert the bytes).
- `/v1/messages/count_tokens` and `/v1/models` are 404 with a good token, and 401 without one (auth first).
- A websocket to `/v1/messages` is closed with 1008.

PoC scenario (`pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_claude_agent_messages.py`, owned by task B2): the Claude Agent SDK against the real remote listener and the fake model server, marked `network` where it needs the 242 MB CLI. It is the live check of the open points below.

Gate for the developer: `git diff --stat main -- packages/chassis/src/chassis/core packages/chassis/src/chassis/ports` prints nothing; `make lint` passes the import rules; `make quick`.

### A.12 Open points

- **The CLI's reaction to errors is untested.** The capture sent no 4xx or 5xx. Whether the CLI honors `x-should-retry`, how it retries a 429, and how it reacts to `event: error` are unknown. B2 must test each row of A.8 against the real CLI before this table is final.
- **`stop_reason: max_tokens` cannot be sent.** `ModelChunk` has `finish: bool` and no reason. A truncated answer looks complete. The fix is a `finish_reason` on the port, which is a port change with a contract-suite case for the fake and for `LiteLLMModel`. Not in this work.
- **`message_start` carries `input_tokens: 0`.** The CLI's cost display may read it. Check against the CLI's reported totals in B2. Fall back to an estimate from the request size if it matters.
- **Long sessions** may add compaction, title, or `count_tokens` calls. The capture ran 1 to 4 turns. Rerun it on a CLI bump, as the capture note says.
- **The Bash tool's child sees the token.** The capture saw `ANTHROPIC_AUTH_TOKEN` in the child's environment. What it opens is ADR-005's blast radius: one chassis's proxies, from the remote's pod, inside a run. A per-run token would shrink it. That is `platform-security`'s call, not this route's.
- **Context length on an SLM.** A 15k-token tool list plus history may pass a small model's window. The upstream 400 becomes `model_rejected_request` with fixed text, so the CLI gets no context-length message to compact from; whether it then compacts anyway is a PoC-6c finding. If it needs the number, send a fixed phrase for a context-length error, not the upstream text.

---

## B. Plain-A2A mode in the `remote` connector

### B.1 Decision

Add an opt-in field `spec.engine.protocol` (suggested: the name), with values `chassis` (the default) and `a2a`. It is allowed only with `spec.engine.connector: remote`. In `a2a` mode the connector does not look for `metadata["chassis.event"]`. It reads the third-party agent's own A2A stream and builds chassis events from it. The chassis mode is untouched.

The code is a new module, `chassis.adapters.a2a.plain`, plus two small hooks in `A2AConnector`. It is chassis-only. `mapping.py` is not edited, so the copy in `packages/workload-a2a` stays byte for byte equal with no work. The target for PoC-6b is `kagent-adk`, kagent's Python agent runtime (Google ADK, served over A2A), run as a `remote` workload in a gVisor pod. The orchestrator ruled out full kagent on Kubernetes on 2026-10-09.

### B.2 Reasons

- Third-party agents (kagent-adk, managed runtimes) will not emit `metadata["chassis.event"]`. The probe ran the real `RemoteConnector` against kagent-adk: the card, the stream, and the bearer worked, and the connector returned `end ok` with `output: null`. The answer text was dropped, because the connector reads text only from `chassis.event`. `COMPLETED` already became `end ok` through `_from_state`. So the wire is compatible and the mapping is the gap.
- A new reading side, not a new lane. The lane is about trust and the network path (ADR-001). The protocol is a property of the remote. Keeping them apart avoids a fourth `Lane` value, which would touch `chassis.ports.engine`.
- One config field, restart-only, refused anywhere it does not apply. A chassis-mode remote cannot be put into plain mode by the remote.

### B.3 Rejected

| Option | Why not |
| ------ | ------- |
| Put the plain rules in `mapping.py` | The file is copied into the template server. It would ship third-party quirks to every workload and force a change in both copies and in the TypeScript server for something a workload never does |
| Detect the mode: plain when no `chassis.event` shows up | A chassis workload with a bug would be quietly read as plain, with no `tool_call` and zero usage. Worse, the remote would choose the mode. The operator chooses |
| A new lane or connector name `a2a` | Touches `Lane` in `chassis.ports.engine`, a port. The trust rule is per lane, and this is not a trust change |
| A card extension that says "I speak chassis events" | Third-party cards do not have it. It could be a later way to pick the default, not a way to read kagent |
| A shim workload in front of the third-party agent | An extra pod and hop, and a second thing to admit and secure. It stays the fallback for an agent that needs real logic, not text |
| Map the agent's data parts to `tool_call` | The probe did not capture them (`event_converter.py` was not read in detail). Guessing a wire format is how a mapping drifts. Not visible for now |

### B.4 Config

```yaml
spec:
  engine:
    connector: remote
    protocol: a2a                       # chassis (default) | a2a
    url: http://kagent-adk.poc06-remote.svc:8080
    auth: {scheme: bearer, token_env: REMOTE_KAGENT_ADK_TOKEN}
    a2a:                                # optional; only with protocol: a2a
      usage_key: kagent.dev/a2a/usage   # default null: usage is zeros
      context_id: omit                  # omit (default) | trace_id
```

suggested: every name and default. `extra="forbid"` on `a2a`.

| Field | Rule |
| ----- | ---- |
| `protocol` | `chassis` or `a2a`. Default `chassis`. Restart-only |
| `a2a.usage_key` | A string or null. Names the metadata key that holds token usage. A flat lookup, so dots and slashes are part of the name. Null (the default) means the agent's usage is not read. No vendor key is built in |
| `a2a.context_id` | `omit`: the request has no `context_id`, so the remote starts a new session per run. `trace_id`: the chassis sets it to the run's trace id, as in chassis mode |

Refusals at load, naming the field:

- `protocol: a2a` with a connector other than `remote`: `spec.engine.protocol: a2a is for spec.engine.connector: remote only (got '<lane>')`.
- `a2a` set while `protocol` is `chassis`: `spec.engine.a2a needs spec.engine.protocol: a2a`.

`as_mapping()` leaves `protocol` and `a2a` out when the lane is not `remote`, so `sidecar` and `inprocess` `setup` see the v4 mapping.

`spec.trust` is independent. A plain remote may be `trusted` or `untrusted`.

`context_id: omit` is the default because a repeat of the same trace id would otherwise continue the same remote session. The gateway of full kagent treats a context id as an existing Session (probe note; `InteractionService.PrepareSend` was not read, so this is unverified). kagent-adk accepted `contextId` = trace id in the probe run. The default is chosen for a stateless run; the other value is there for an agent that wants the trace id for correlation.

### B.5 The request side

`A2AConnector` gets the hook `_message_for(request, ctx) -> SendMessageRequest`. The default calls `request_to_message` as today. In plain mode `RemoteConnector` calls `plain_message(...)`:

- One text part with `input.text`, when set.
- One data part with `input.data`, when not empty (the A2A-native view, as in chassis mode). The benchmark tasks have `data == {}`. How kagent-adk reads a data part is unverified.
- `context_id` per the config.
- **No metadata.** `chassis.ctx`, `chassis.input`, and `chassis.schema_version` are not sent. `chassis.ctx` holds the agent name, the idempotency key, the budget, and the versions. A third party has no use for them. The probe saw the default mode send all three to kagent-adk.
- The `traceparent` HTTP header still goes on every request of the run, through `ClientCallContext.service_parameters`, as today. The probe measured that kagent-adk passes the trace id on to its model call. That is how `RequireRun` sees the call.
- `message_id` is a new UUID, as today.

### B.6 The reading side

`A2AConnector` gets the hook `_translator_for(request) -> EventTranslator`. A translator is made per run and is fed each `StreamResponse`:

```python
class EventTranslator(Protocol):
    def feed(self, response: StreamResponse) -> list[dict[str, Any]]: ...
    @property
    def server_finished(self) -> bool: ...
```

The default, `ChassisTranslator`, wraps `update_to_event`: it returns zero or one raw event and raises `BadJson` as today. `server_finished` is true when the last event was `end` or `error`, which is the current `server_done` rule. `run` loops over the list that `feed` returns, validates each event with `parse_event` as today, checks `start.request_id`, yields, and returns after the first `end` or `error`. After the loop `server_done` is `translator.server_finished`. Chassis mode behaves as before. The existing connector tests are the guard.

`PlainTranslator` (in `plain.py`) implements the table below. It never reads `metadata["chassis.event"]`. A remote cannot forge `tool_call`, `end` with status `retry` or `fallback`, or any chassis error code by sending that key. It never reads `task.history`, which holds the user's own message.

**What it reads.** Text parts only (a part with `text` set). Text parts of one message or artifact event join with `""` (suggested: no separator, so chunks of a stream join as the sender wrote them). Data, file, and URL parts are ignored.

| A2A stream item | Becomes |
| --------------- | ------- |
| Any first item | `start {request_id}` is emitted first, once. The id is the request's, so `a2a.request_mismatch` cannot fire |
| `task` in a non-terminal state (the first item of a stream: `SUBMITTED`) | Nothing more. History is not read |
| `status_update`, state `WORKING` (or `SUBMITTED`, `UNSPECIFIED`), with text in its message | `delta` with that text |
| `status_update`, non-terminal, with no message, or a message with no text parts | Nothing (kagent sends one empty `WORKING` message before `COMPLETED`) |
| `artifact_update`, text, not `last_chunk` | `delta` with that text. The first event of an artifact has no `append`, later ones have `append=true`. The flag does not change the result: every such event is a delta |
| `artifact_update` with `last_chunk=true` and `append=true` | `delta`. It is the last piece of a stream, as A2A defines |
| `artifact_update` with `last_chunk=true` and no `append` | **Not a delta.** It is a snapshot of the whole artifact (kagent sends one that repeats the full text). The text is kept for the terminal event |
| `status_update` or `task`, state `COMPLETED` | `metrics`, then `end {status: ok}`. `output` is `{"text": <text>}` **only if no delta was sent**: the snapshots in the order first seen, else the terminal message's text. If a delta was sent, `output` is not set and the collector joins the deltas |
| `task` that is terminal, as the unary answer | The artifacts' text parts are snapshots; the status message's text is the fallback. Handled as `COMPLETED` above. A `task` that arrives after streamed deltas is a snapshot too and changes nothing |
| `message` (a reply with no task) | Its text is the snapshot; handled as `COMPLETED`. kagent does not send this shape; it is unit-tested only |
| `FAILED` or `REJECTED` | `metrics` if usage was seen, then `error {code: "a2a.failed", retryable: false}`. The message is the status message's text, else `the task ended in state <STATE>` (v1's text) |
| `CANCELED` | `error {code: "a2a.canceled", retryable: false}`. As in chassis mode, where the connector never asks for `cancelled=True`, so this is always an error |
| `INPUT_REQUIRED` or `AUTH_REQUIRED` | `error {code: "a2a.unsupported_state", retryable: false}`, message `<STATE> is not in the contract`. The contract has no pause. The code is v1's, so `status_for` and `PUBLIC_MESSAGES` need no new entry |
| An SSE `: ping` comment | Never reaches the mapping. a2a-sdk drops it. It keeps the HTTP read alive and does not extend the run deadline |
| After a terminal item | Ignored. The translator is finished |

A stream that ends with no terminal state is not the translator's: the connector emits `a2a.transport` as today.

**`metrics`.** One event before the terminal event of a `COMPLETED` run, and before `FAILED` or `REJECTED` only when usage was seen. Fields: `input_tokens`, `output_tokens`, `attempt: 1`; `cost_usd`, `model_route`, and `latency_ms` unset.

- Usage is read from the key `a2a.usage_key`, on `status_update.metadata`, `artifact_update.metadata`, `artifact.metadata`, and `task.metadata`. The value is an object. The input count is the first integer found under `promptTokenCount`, `prompt_tokens`, `input_tokens`, `inputTokens`; the output count under `candidatesTokenCount`, `completion_tokens`, `output_tokens`, `outputTokens` (suggested: this alias list). A value that is not an integer is skipped.
- **The last value wins; values are never summed.** kagent reports the same totals on the final artifact and the final status. Summing would double them.
- No key configured, or none found: zeros. **Zeros mean unknown, not none.** The event cannot say it. So the run's span gets `a2a.usage_known=false` (suggested), and a plain remote that goes through the chassis model proxy has its real token count in the run's budget (`spent`), which is the better number. A future change could copy the proxy's count into the response. It is not in this design.

**`tool_call` is not visible.** The agent's tool calls, if any, never become events. A plain run has `delta`, `metrics`, `end`, `error`, and the synthesized `start`.

**Ordering by construction.** `start` first and once, one terminal event last, nothing after. The server-side checks that enforce the order for a chassis workload (`workload.bad_order`, `workload.no_end`) do not exist for a third party, so the translator is what holds the order.

**`server_finished`** is true after `COMPLETED`, `FAILED`, `REJECTED`, and `CANCELED`. It is false after `INPUT_REQUIRED` or `AUTH_REQUIRED`: the task is paused on the remote, not finished, so the connector sends `CancelTask` and the task does not hang. (The chassis-mode rule sets `server_done` for `a2a.unsupported_state` too, which skips the cancel. That is a small bug there; it is not fixed here because it is outside this change. See "Open points".)

### B.7 Card pinning and the bearer

Unchanged, and confirmed in `remote.py`:

- `_check_card` keeps only the interface whose binding is JSON-RPC, replaces its URL with `spec.engine.url`, and keeps its `protocol_version`. A card with no JSON-RPC interface is refused (`ValueError`, which `setup` turns into `RuntimeError: the remote at <url> is not reachable: ValueError`). A card that names another host is not followed; the pin is why a third-party card cannot redirect the token. In plain mode the card is third-party-controlled, so the pin matters more, not less.
- The kagent card the probe saw passes: the gateway always emits a JSON-RPC interface, and the pinned URL was `spec.engine.url`.
- Because `protocol_version` is kept, an agent on A2A 0.3 is read through a2a-sdk's compatibility transport (`CompatJsonRpcTransport`, chosen for a legacy version). That is in the SDK; it is unverified against a real 0.3 agent.
- The card's `security_schemes` and `security_requirements` are ignored. The connector sends `Authorization: Bearer <token>` on the card fetch, every message, the cancel, and the probe, as v4 says. `spec.engine.auth` stays required for `remote`. kagent-adk ignores the header; the model call back to the chassis is what uses it. With kagent's `apiKeyPassthrough` the inbound `Authorization: Bearer` becomes the model API key, so the same remote token reaches the chassis remote listener, and the `traceparent` rides along (probe: measured).
- `sigv4`, `google`, and other schemes stay out of scope (no cloud runtime in PoC-6).
- `probe()` is unchanged.

kagent-adk calls the OpenAI-compatible route (`POST /v1/chat/completions`) on the remote listener. It does not need part A. Part A is for the Claude Agent SDK.

### B.8 What the chassis cannot see or control with a plain remote

For the PoC-6b list (exit criterion 6). Mark `suggested:` where it is an inference.

- **No inbound check.** kagent-adk checks no bearer. The token on the A2A call is ignored. NetworkPolicy by label is the only control on who calls it. ADR-005 calls the token the second, independent check; for this remote it does not exist.
- **Tool calls.** Not visible. The tools the agent uses and their results are in its own logs.
- **Usage.** Self-reported through one metadata key, or unknown. The proxy's budget count is the reliable number for model tokens.
- **Prompts and reasoning text.** ADK may mark thought parts in part metadata (unverified). Plain mode treats every text part as the answer.
- **Sessions.** kagent-adk keeps its own task store and session (in memory here). The chassis cannot clear it. `context_id: omit` limits the reuse.
- **Telemetry.** With default OTel settings the probe saw `COMPLETED` come 16.8 s after the last chunk, and 0.64 s with the three OTel exporters set to `none`. Likely a flush to an unreachable collector; not root-caused. Set them to `none` on the pod.
- **Cancel.** Whether kagent-adk stops work on `CancelTask` is untested.
- **Size.** No cap on the text a remote returns, as in chassis mode.

### B.9 Files

| File | Change |
| ---- | ------ |
| `packages/chassis/src/chassis/adapters/a2a/plain.py` (new) | `PlainTranslator`, `plain_message`, `PlainOptions`, the usage reader. Imports a2a-sdk, protobuf's `json_format`, and `chassis.core.events.SCHEMA_VERSION`. Imports nothing private from `mapping.py` |
| `packages/chassis/src/chassis/adapters/a2a/connector.py` | The hooks `_message_for` and `_translator_for`, `ChassisTranslator`, and the loop over a translator's list. No change in chassis mode |
| `packages/chassis/src/chassis/adapters/a2a/remote.py` | `setup` reads `protocol` and `a2a`, builds `PlainOptions`, overrides the two hooks, sets span attributes `a2a.protocol` and `a2a.usage_known` |
| `packages/chassis/src/chassis/server/config.py` | `EngineSpec.protocol`, `PlainA2ASpec`, the two refusals, `as_mapping` |
| `packages/chassis/schemas/chassis-config.v0.json` | Regenerated with `make schemas`: `spec.engine.protocol` (enum, default `chassis`) and `spec.engine.a2a`. Optional, so the major stays `0` |
| `packages/chassis/src/chassis/adapters/a2a/__init__.py` | Export if needed |
| Docs | `docs/guides/chassis-reference.md`; the contract v5 text |

Not touched: `mapping.py` and `packages/workload-a2a/src/workload_a2a/mapping.py` (they stay byte equal, so the existing test stays green), `adapters/a2a/server.py`, `sidecar.py`, `inprocess.py`, `packages/workload-a2a`, anything in `chassis.core` or `chassis.ports`, the TypeScript server.

If a later change needs a shared helper in `mapping.py`, it changes both copies in one commit and `test_workload_a2a_copies.py::test_mapping_equals_the_chassis_copy_byte_for_byte` enforces it. This design needs none.

### B.10 Tests

- `packages/chassis/tests/test_a2a_plain.py` (new, pure, built from `StreamResponse` protobufs): the kagent sequence from the probe, item by item: `task SUBMITTED`, `WORKING`, a first artifact chunk with no `append`, five chunks with `append=true`, the `last_chunk` snapshot with usage, an empty `WORKING` message, `COMPLETED` with usage. Expect `[start, delta x6, metrics(42, 9), end ok]` and no `output`. Also:
  - only a snapshot and no deltas: `end.output` is `{"text": ...}`, no `delta`;
  - the unary `task`, `COMPLETED`, text in `artifacts[0]`: `[start, metrics, end]` with `output`;
  - a terminal status message as the only text; a `message` reply;
  - `last_chunk` with `append=true` is a delta;
  - `FAILED` with and without a message; `REJECTED`; `CANCELED`; `INPUT_REQUIRED` and `AUTH_REQUIRED` with `server_finished` false;
  - data parts ignored; `task.history` ignored; `metadata["chassis.event"]` ignored (a forged `tool_call` produces nothing);
  - usage under each alias; a non-integer skipped; the last value wins; no `usage_key` gives zeros; usage on the final artifact and the final status is not doubled;
  - a second terminal item and any item after a terminal are ignored;
  - the order invariant: for every sequence above, `start` is first and once and exactly one terminal event is last.
- `packages/chassis/tests/test_remote_connector_plain.py` (new, over a Unix socket like `test_lane_contract.py`): a small a2a-sdk server that emits plain events (no `chassis.event`).
  - The connector yields the events above and the `Response` has the answer text (the probe's `output: null` bug has a regression test here: the text is not empty).
  - The request has no `chassis.*` metadata, no `context_id` by default, the trace id with `context_id: trace_id`, a text part, and a data part only when `data` is not empty.
  - The bearer is on the card fetch, the message, the cancel, and the probe.
  - A card that names another host: every request still goes to `spec.engine.url`.
  - A card with only a gRPC interface is refused.
  - `INPUT_REQUIRED` sends `CancelTask`; `COMPLETED` does not.
  - The deadline gives `a2a.timeout`; a dropped stream gives `a2a.transport`.
  - Chassis mode is unchanged: the existing `test_remote_connector.py` and `test_lane_contract.py` pass without edits.
- `packages/chassis/tests/test_server.py` or `test_config_loader.py`: the two refusals and their text; the defaults; `as_mapping` for `sidecar` has no `protocol`; the schema drift test after `make schemas`.
- `packages/workload-a2a/tests/test_workload_a2a_copies.py`: unchanged; add `test_plain_mode_is_chassis_only`, asserting `workload_a2a` has no `plain` module.
- PoC-6b B4, `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kagent_adk_lane.py`: the lane contract's observable subset (start first, deltas join to the answer, `end ok`, `metrics`, order) against kagent-adk, in memory with a stub and on kind with the real runtime. Not the full `LaneContract`: `tool_call` and the `retry` and `fallback` statuses cannot appear.

### B.11 Open points

- **Everything kagent-specific rests on one probe** of kagent-adk 0.4.0 (a2a-sdk 1.1.5) with a fake model. Failure, tool-call, and input-required replies were not captured. Run B4 and compare each row of B.6 with the real stream.
- **`context_id`.** Unverified for the gateway. For kagent-adk direct, `omit` is expected to work, since the SDK server assigns one; confirm in B4.
- **Data parts.** How kagent-adk handles an `input.data` part is unknown. The benchmark tasks do not send one.
- **Thought text.** ADK may emit reasoning as text parts. If it does, the answer will contain it. Check in B4 against a real model.
- **The cancel on input-required** is skipped in chassis mode (`server_done` is set for any `error`). Decide whether to fix it in `ChassisTranslator` (a one-line behavior change, outside this design).
- **The 16.8 s delay** is not root-caused.

---

## C. The freeze (exit criterion 8)

### C.1 Decision

Do the engines need an event change? **No.** Criterion 8 is met by freezing, not by listing changes. The check is in C.4.

**"Frozen as v1" is read as "frozen as the first stable line", not as the string `"1"`.** `schema_version` stays `"0"`, `SUPPORTED_SCHEMA_VERSIONS` stays `("0",)`, no `events.v1.json` is made, and no file is renamed. From contract v5 on, the surface in C.2 changes only by the rules in C.3. The first breaking change bumps `schema_version` to `"1"` and keeps `"0"` accepted, as contract v1 already says and the hard rule requires.

Four things carry "v1" today, so this reading is written down to stop a search for `events.v1.json`: contract v1 (a document), the A2A mapping's "v1 changes" (a PoC-2 decision), `task-result.v1.json` (the result event payload, which has its own major), and the topics `agents.task.completed.v1` and `agents.task.failed.v1`. The criterion's "v1" is none of them. Say it in ADR-006 and in the 6b README.

### C.2 What is fixed

Fixed means: the description below is the contract, and a test pins it (C.4).

1. **`handle`.** `async def handle(input: dict, ctx: dict) -> AsyncIterator[dict]` on the wire; the typed `Handle` alias is internal. A yielded event with no `schema_version` is filled with `"0"`. The order: `start` first and once, `end` or `error` last, nothing after. `handle` forwards `ctx["traceparent"]` as the `traceparent` header on every model and MCP call. It calls only the proxy listeners.
2. **`TaskInput`** (`task_input.v0.json`): `text: str | None`, `data: object`, `extra="forbid"`.
3. **`Context`** (`context.v0.json`): `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version`, `budget {max_tokens, timeout_ms}`, `versions`, `model_route`, `traceparent`. Passed through unchanged. The remote's bearer token is never in it.
4. **The six events** (`events.v0.json`) with their fields: `start {request_id}`, `delta {text}`, `tool_call {call_id, name, arguments, result?}`, `metrics {input_tokens, output_tokens, cost_usd?, model_route?, latency_ms?, attempt}`, `end {status: ok | retry | fallback, output?}`, `error {code, message, retryable}`. Every event has `schema_version`. Unknown fields are refused. `end.output` replaces the joined deltas when set. The collector sums `metrics`.
5. **The envelope:** `Request` and `Response` (`request.v0.json`, `response.v0.json`).
6. **The A2A mapping in chassis mode.** The four metadata keys (`chassis.event`, `chassis.ctx`, `chassis.input`, `chassis.schema_version`) as JSON strings; one A2A event per chassis event, in order; `context_id` is the trace id; the event-to-state table; the `traceparent` HTTP header; the cancel; the native parts as the view for generic clients. The v0 `Struct` form of those keys stays readable. It is reader-only compatibility, no writer produces it, and it is dropped only at the next major (contract v1 said "dropped in v2" and the code never did; this keeps the code's behavior).
7. **The error codes of contract v1** with their `retryable`: `a2a.unsupported_schema_version`, `a2a.bad_request`, `workload.bad_event`, `workload.bad_order`, `workload.no_end`, `workload.exception`, `a2a.bad_event`, `a2a.request_mismatch`, `a2a.timeout`, `a2a.transport`, `a2a.canceled`, `a2a.failed`, `a2a.unsupported_state`, `engine_error`. A workload's own codes stay a convention.

Not frozen, and why:

- **The plain-A2A mode (part B).** It was read against one agent. It is an opt-in reading side, not the chassis event contract. It becomes part of the freeze when a second plain agent has passed.
- **The model proxy's wire formats (part A), the public interfaces, the config (`chassis-config.v0.json`), the manifest, the ports, `ToolPort`, and the result events.** Each has its own major or is a chassis-side surface. They stay additive under their own rules.
- **The workload's own error codes.**

### C.3 What counts as breaking from now on

The two directions differ. Be exact.

**Events go from the workload to the chassis, and every reader is strict** (`extra="forbid"` in the chassis, `additionalProperties: false` in `workload_a2a`'s validator, the TypeScript validator). So for events, **a change is breaking when an older reader would refuse the stream**, even if it looks additive:

- a new event type;
- a new field on an event, even an optional one;
- a new value of an enum (`end.status`);
- a removed or renamed field, a field that becomes required, or a changed type;
- a changed meaning of a field, a changed order rule, or a changed `retryable` or meaning of a frozen error code.

A breaking change bumps `schema_version` to `"1"`, adds `"1"` to `SUPPORTED_SCHEMA_VERSIONS`, keeps `"0"`, publishes `events.v1.json`, and runs `make schemas`. How the chassis learns which version a workload accepts, so it does not send `"1"` to a workload that knows only `"0"`, is part of that bump's design, not decided now (see "Open points").

**`Context` and `TaskInput` go from the chassis to the workload.** The chassis writes them and the workload reads them. A new optional field is **compatible** if workloads ignore keys they do not know. `traceparent` was added this way in contract v1 with no bump. The rule from now on: a workload must ignore unknown keys in `ctx` and `input`.

**Compatible, no bump:**

- a new optional `Context` field;
- a new error code (codes are open strings; the first new code with a public meaning also gets a `PUBLIC_MESSAGES` entry);
- a new optional `spec.*` field;
- a new A2A metadata key that the other side ignores;
- a new route, port, interface, or adapter;
- text in a doc.

**Always a new major of the surface that changed:** removing the `Struct` form of the metadata keys; changing the A2A state mapping for a chassis event; changing `handle`'s wire signature.

### C.4 The evidence, and the one list of changes

**The mappings need no event change.** Read:

- The four built workloads (`echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-typescript`) map their frameworks to `start`, `delta`, `tool_call`, `metrics`, `end`, and `error` with the same codes. The TypeScript agent emits `tool_call` with `result` and its order matches `echo-python`.
- The plain mapping (part B) produces `start`, `delta`, `metrics`, `end`, and `error`, all valid under `events.v0.json`.
- The Claude Agent SDK's workload (B2) has text, tool use, and a result message with usage and cost. They map to `delta`, `tool_call`, and `metrics` (`cost_usd`, `latency_ms`). The capture shows nothing the six events cannot carry.

**What PoC-6 does change, none of it in the frozen surface:**

| Change | Where | Surface |
| ------ | ----- | ------- |
| The Anthropic proxy route | A | Model proxy |
| The 403 `model_route_denied` | A.9 | Model proxy |
| The Anthropic bodies for the two middleware refusals | A.8 | Remote listener |
| `spec.engine.protocol` and `spec.engine.a2a` | B | Config, A2A connector |

**What an engine could still want**, listed so nobody discovers it in B6. None blocks the freeze, and none is done:

| Wish | Why it is not in | What it would take |
| ---- | ---------------- | ------------------ |
| A pause event for input-required or approval | No benchmark task needs it. Plain mode maps `INPUT_REQUIRED` to `a2a.unsupported_state` | A new event type: breaking, so schema `"1"` |
| Reasoning text apart from the answer | No engine needs it for a task | A new event or a `delta` field: breaking |
| Usage known or unknown on `metrics` | Plain mode sends zeros. A flag fits the span and the proxy's count better | A new `metrics` field: breaking for strict readers |
| Cache-token counts on `metrics` | No criterion uses them | A new field: breaking |
| A finish reason (`max_tokens`) in the answer | It is a `ModelPort` gap, not an event gap | A port change (A.12) |
| Unknown fields allowed on read | It would make later additive event changes free of a bump | A change to the chassis, `workload_a2a`, and the TypeScript validator. It trades away typo catching. Decide at the first additive need |

**The machine check for criterion 8** (task B6, run once more at the end of W3 against the real engines):

1. `git diff --stat main -- packages/chassis/src/chassis/core packages/chassis/src/chassis/ports packages/chassis/schemas/events.v0.json packages/chassis/schemas/task_input.v0.json packages/chassis/schemas/context.v0.json packages/chassis/schemas/request.v0.json packages/chassis/schemas/response.v0.json` prints nothing.
2. `packages/chassis/tests/test_contract_freeze.py` (new; suggested name) passes. It pins:
   - the sha256 of the five schema files (hashes at `5860257`, first 16 hex: `events.v0` `60de41563a1d0582`, `task_input.v0` `6d8aa949b462c77e`, `context.v0` `d2db0e94a50379f1`, `request.v0` `4ff8a4324a90e8c0`, `response.v0` `63acf4f16dae59f9`; the test holds the full digests);
   - the set of event types is the six, `SCHEMA_VERSION == "0"`, `SUPPORTED_SCHEMA_VERSIONS == ("0",)`;
   - the four metadata key constants in `mapping.py` and the event-to-state table;
   - the set of frozen error codes, as strings the connector and `workload_a2a` can emit.
   Changing any of them fails the test. The fix is a bump and a new contract, or a revert. That is what "frozen" means in the repo.
3. Every engine's mapping test parses every event it emits with `parse_event`.
4. The existing byte-equality test of `mapping.py` and the TypeScript twin check (`make ts-check`) pass.

If any engine turns out to need an event change after all, B6 lists it in `pocs/poc-06b-bake-off-remote-lane/notes/backlog-changes.md`, the freeze criterion is met by the second clause ("or the changes they needed are listed"), and criterion 2 is judged on the diff in step 1.

### C.5 Rejected

| Option | Why not |
| ------ | ------- |
| Bump to `"1"` with the same content | A rename. It forces `Literal["0"]` to change in `chassis.core.events`, and `SCHEMA_VERSION` in `mapping.py` (both copies), the vendored schema in `workload_a2a`, and the TypeScript server. A chassis that speaks `"1"` makes every deployed `"0"`-only workload answer `a2a.unsupported_schema_version`. The cost is large and the information is none: `schema_version` counts breaking changes (contract v1), it is not a maturity label. It also puts a change in `chassis.core` into the criterion 2 diff |
| Freeze nothing and write only a list | Criterion 8 asks for a freeze first. With no engine needing a change, the list is empty and the freeze is the real work |
| Freeze the plain mode too | One probe is not enough evidence |
| Freeze the model proxy formats | They are chassis-side, they are already additive under contract v4, and the Anthropic route is not built |

### C.6 Open points

- **Version negotiation.** The chassis sends `chassis.schema_version: "0"` and the server refuses a version it does not know. When `"1"` exists, a chassis that wants to send it needs to learn what the workload accepts (a card extension, or a field on the card). Decide in the bump's design.
- **Strict readers.** The cost of every additive event change being a bump is a decision to revisit at the first additive need (the last row of the table above).
- **ADR.** The freeze meaning ("first stable line, major stays `0`") and the plain-A2A choice are decisions. They go into the bake-off ADR (suggested: ADR-006, task A5 and B6) with the reasons here. They are not an ADR of their own, because this task writes the design document only.

---

## Config reference

New fields. suggested: every name and default. `chassis-config.v0.json` keeps its major: every new field is optional or has a default.

| Field | Default | Reloads |
| ----- | ------- | ------- |
| `spec.engine.protocol` | `chassis` (`remote` only for `a2a`) | No |
| `spec.engine.a2a.usage_key` | `null` | No |
| `spec.engine.a2a.context_id` | `omit` | No |

No new `RELOADABLE` path. `spec.engine` is restart-only already. The constants `FIRST_CHUNK_WAIT_S` (5.0) and `PING_INTERVAL_S` (15.0) are module constants in `model_proxy_messages.py` (suggested), not config.

## Cost per lane

- **`inprocess`.** Nothing. The Anthropic route exists on the proxy and nothing calls it. `spec.engine.protocol: a2a` is refused.
- **`sidecar`.** The Anthropic route exists on the loopback listener, with no auth, as the chat route. No new hop and no new credential. `spec.engine.protocol: a2a` is refused.
- **`remote`.** Part A adds no hop: the Claude Agent SDK's CLI calls the remote listener as a chat workload does, with the same token and the same `RequireRun`. It adds a hold of up to 5 s before the first byte on a stream (only when the model is slow to answer) and a ping every 15 s. Part B adds no hop and no credential. A plain remote shows the chassis less: no `tool_call`, unknown usage, no inbound token check on kagent-adk. The gVisor overhead is as measured in PoC-5.

## Contract suites

| Suite | New or changed | Bound to |
| ----- | -------------- | -------- |
| `ModelPortContract` | Unchanged. The new route is a client of the port, not a port | `ScriptedModel`, `LiteLLMModel` |
| `LaneContract` | Unchanged for chassis mode. A plain-mode subset (B.10) is added beside it | `remote` over a Unix socket with a plain server; kagent-adk in 6b |
| Freeze test | New (C.4) | The five schemas, the six events, the key constants, the codes |

## Links

- Plan and planning doc: [the PoC-6 work plan](../plans/2026-10-09-poc-06-bake-off.md), [PoC-6](../planning/poc/006-PoC-6-framework-bake-off.md).
- Decisions it rests on: [ADR-001](../planning/adr/001-chassis-delivery-model.md) (lanes, hard requirement 1, item 8), [ADR-003](../planning/adr/003-chat-formats-onto-the-canonical-request.md) (carry, refuse, ignore; the public Anthropic interface), [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md) (the remote token, `RequireRun`, the card pin).
- Evidence: [the Claude CLI capture](../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-claude-cli-capture.md) and its scripts in `pocs/poc-06b-bake-off-remote-lane/notes/capture/`; the kagent probe note named at the top.
- The contract in force: [contract v4](contract-v4.md).
