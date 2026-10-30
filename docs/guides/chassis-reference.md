# Chassis reference

Status: written 2026-10-09, moved out of `packages/chassis/CLAUDE.md`. The code wins where this guide and the code differ.
The agent brief is [packages/chassis/CLAUDE.md](../../packages/chassis/CLAUDE.md). It holds the rules. This guide holds the per-module detail.

Paths under `chassis/` are `packages/chassis/src/chassis/`. Paths under `configs/`, `schemas/`, and `tests/` are under `packages/chassis/`.

## History and spec

- Day 0 holds the contract and the fakes.
- The walking skeleton adds the server.
- PoC-3 adds the public interfaces.
- PoC-4 adds state, events, the config store and reload, idempotency, readiness, and the drain. Spec: [contract v3](../contracts/contract-v3.md). How it works: [the PoC-4 guide](poc-04-how-it-works.md).
- PoC-5 adds the `remote` lane, the remote proxy listener, `ToolPort` write mode, the MCP gateway adapter, the uncorrelated cap, and `spec.trust`. Spec: [contract v4](../contracts/contract-v4.md). How it works: [the PoC-5 guide](poc-05-how-it-works.md). See [PoC-5 modules](#poc-5-modules).
- PoC-6 adds the plain-A2A mode of the `remote` lane and the Anthropic proxy route. Spec: [contract v5](../contracts/contract-v5.md). How it works: [the PoC-6 guide](poc-06-how-it-works.md). See [PoC-6 modules](#poc-6-modules).
- PoC-7 adds security, observability, feedback, and the evaluator gate. Spec: [contract v6](../contracts/contract-v6.md). See [PoC-7 modules](#poc-7-modules).
- PoC-8 adds `/metrics`, JSON logs, the router span, dashboards, rate limits, the stream secret check, the output URL allow-list, and the rest of the marking. Spec: [contract v7](../contracts/contract-v7.md). See [PoC-8 modules](#poc-8-modules).
- The contract in force is named once, on the `Spec:` line of [packages/chassis/CLAUDE.md](../../packages/chassis/CLAUDE.md). Each version names only its changes; read the earlier ones for the rest.

## core

`chassis/core/` has no network, no product SDK, and no web framework. `make lint` enforces it.

- `trace.py` is stdlib only.
  - `trace_id_hex` and `traceparent_for` build the W3C `traceparent` from `ctx.trace_id` and the run span.
  - `parse_traceparent` reads the trace id from the inbound header. The model proxy and the MCP adapter share it.
- The envelope: `Request`, `Response`, `Context` (with the optional `traceparent`), `TaskInput`, `Versions`.
- The events: `Start`, `Delta`, `ToolCall`, `Metrics`, `End`, `Error`.
- `collect()`, the `Handle` type, and `echo`.
- `inbound.py` is the inbound adapter seam.
  - Types: `Ids`, `Served`, `ReplyMeta` (with `for_request` and `collect`), `Reply`, `StreamReply`, `Refused`.
  - The `InboundAdapter[BodyT]` Protocol has the pure `to_request`. It has the back-mapping `complete`, `stream`, and `error`.
  - `Interface`.
  - `status_for(code, retryable)` holds the PoC-3 status table. It maps `engine_error` and `internal_error` to 500.
  - PoC-4 adds 422 `idempotency_conflict`, 409 `idempotency_in_progress`, and 503 `state_unavailable`.
  - `PUBLIC_MESSAGES` and `public_message(code)` are the fixed client-facing error text. suggested: the text.
  - `answer_text` is the answer a chat format shows. `rest_of_answer` is what a stream sends after its deltas.
- `manifest.py` is `Manifest` v0: `ManifestAgent`, `HttpInterface`, `McpInterface`, `OpenAPIRef`. suggested: the whole shape.
- `results.py` is `TaskResult`, the result-event payload.
  - `TASK_COMPLETED` is `agents.task.completed.v1`.
  - `TASK_FAILED` is `agents.task.failed.v1`.

## ports

`chassis/ports/` holds one `typing.Protocol` per dependency.

- `ModelPort`, `EngineConnector`, `ConfigPort`, `TelemetryPort`.
- `EngineConnector.probe()`.
- `ConfigUnavailable` lives in `config.py`.
- `ToolPort`.
  - `ToolDefinition` has a JSON Schema `parameters`.
  - `ToolResult`.
  - `ToolError` has a `code` and a `retryable` flag. A failure the tool itself reports is `ToolResult(is_error=True)`, never a `ToolError`.
    - `unknown_tool`: no such tool. Not retryable.
    - `bad_arguments`: the arguments fail `parameters`. Not retryable.
    - `idempotency_key_required` (PoC-5): a write tool (`read_only: false`) called without `idempotency_key`. Not retryable.
    - `tool_denied` (PoC-5): the gateway refused the call for this credential. Not retryable.
    - `tool_unavailable` (PoC-5): the gateway or the tool server did not answer, timed out, or failed with a 5xx. Retryable (`retryable=True`).
  - `ToolPort.call` takes an optional `idempotency_key`. A write tool requires it.
- `StatePort` in `state.py` stores bytes by key.
  - Methods: `get`, `set`, `set_if_absent` (with a required TTL), `compare_and_set` (`value=None` deletes), `delete`.
  - Every failure is `StateUnavailable`.
  - `InMemoryState` lives here because the bundle defaults to it.
- `EventPort` in `events.py`.
  - Methods: `publish` and `subscribe(topic, handler, *, group, max_attempts)`.
  - `CloudEvent` is CloudEvents 1.0 structured JSON. Its extensions are `traceparent`, `idempotencykey`, `configversion`, `modelroute`, and `partitionkey`.
  - `PublishFailed`, `DLQ_SUFFIX` (`.dlq`), and `NoEvents`.
- `PortBundle` bundles them.
  - `tools` defaults to `NoTools`.
  - `state` defaults to `InMemoryState`.
  - `events` defaults to `NoEvents`.
- New ports arrive with their first adapter. See `profiles.REGISTRY` for which PoC.

## fakes

`chassis/fakes/` holds the in-memory doubles.

- `ScriptedModel`, `FakeEngine`, `InMemoryConfig`, `InMemoryTelemetry`, `InMemoryTools`, and `InMemoryState` (re-exported).
- `FakeEngine` is a test double, not a lane.
- `InMemoryBus` in `events.py` delivers at least once.
  - It retries in place, then sends to `<topic>.dlq`.
  - It exposes `published` and `fail_next_publish`.
  - Two replicas in a test share one bus.
- `fakes/tool.py` defines the shared read-only `glossary_lookup` once (`GLOSSARY`, `default_tools()`).
  - A small hand-written JSON Schema check validates arguments. It is not `jsonschema`.

## adapters

`chassis/adapters/` holds the real adapters, one package per product. The product SDK is imported only there. `openai` and `anthropic` are imported only under `adapters`. `make lint` enforces it.

### litellm

- `LiteLLMModel` is `ModelPort` over OpenAI-compatible HTTP with httpx.
- `LiteLLMModel.from_env()` builds it from `LITELLM_BASE_URL` and `LITELLM_API_KEY`.
- The key is never logged.
- The httpx client ignores the environment (`trust_env=False`).
- It binds the same contract suite as `ScriptedModel` in `tests/test_contracts.py`. The run is offline, against the fake model server.

### PoC-4 adapters

Each is built by `from_env()` and loaded lazily.

- `valkey/` is `ValkeyState`.
  - It uses valkey-py with `VALKEY_URL`, `VALKEY_USERNAME`, and `VALKEY_PASSWORD`.
  - `compare_and_set` is one Lua script.
  - Errors become `StateUnavailable` with the class name only.
- `s3/` is `S3Config`, registered as `minio` and `s3`.
  - It runs the minio SDK in `asyncio.to_thread` and reads `CONFIG_S3_*`.
  - It reads `<prefix><name>.yaml`.
  - It refuses a document over 256 KiB, one with a YAML alias, or one nested deeper than 64, each as `ConfigUnavailable`. suggested: both caps.
  - `subscribe` polls `stat_object` every `CONFIG_POLL_INTERVAL_S` (suggested 5, ±10% jitter).
  - A failed poll is counted, never raised.
- `kafka/` is `KafkaEvents`.
  - It uses aiokafka with `KAFKA_BOOTSTRAP_SERVERS`.
  - It publishes with `acks=all` and makes 3 publish attempts.
  - It commits after the handler or after the dead-letter publish.
- `dapr/` is `DaprEvents`.
  - It talks httpx to daprd. There is no SDK.
  - It reads `DAPR_API_TOKEN` and `APP_API_TOKEN`.
  - `inbound_routes()` (`/dapr/subscribe`, `/dapr/events/{topic}`) is mounted on the proxy app only.
  - Retries and the dead-letter topic are Dapr's.

### a2a

`a2a/` is the A2A lane. a2a-sdk 1.2 is imported only here.

- `mapping.py` maps events to A2A updates and back.
  - It is pure and has no chassis import.
  - `chassis.event`, `chassis.ctx`, and `chassis.input` cross as JSON strings.
  - The v0 `Struct` form is still read.
- `server.py` has `HandleExecutor`, `PruningRequestHandler`, `build_agent_card`, and `build_app`. It is the template server around a wire-form `handle`.
  - Input comes from `chassis.input` as is. Parts are only a fallback.
  - A bad `chassis.ctx` or `chassis.input` is `a2a.bad_request`.
  - `cancel` only sets a flag. `execute` closes the generator in its own task.
- `connector.py` is `A2AConnector`, the client side every lane shares.
  - It maps back to events.
  - It sends one `traceparent` per run. It goes as the header on every request and in `ctx.traceparent`.
  - `budget.timeout_ms` is both the per-read timeout and a whole-run deadline, in every lane. Running out is `a2a.timeout`, retryable.
  - A transport failure is `a2a.transport`. So is a stream with no `end` or `error`.
  - It cancels even when closing the stream raises.
  - It opens one span per run.
  - It has two hooks a lane can override: `_message_for` builds the A2A request and `_translator_for` makes the run's `EventTranslator`. The default `ChassisTranslator` reads `chassis.event`. The translator also reports the remote's `task_id` and whether the task is finished there. The connector cancels only a task that is not finished.
- `inprocess.py` is `InProcessConnector`. It runs that server in this process over `httpx.ASGITransport`.
  - Events arrive in one batch.
  - The run deadline ends the stream but not `handle`.
  - `InProcessConnector.probe()` is `True`.
- `sidecar.py` is `SidecarConnector`. It calls the workload's own server at a loopback `spec.engine.url`.
  - It uses TCP or the `spec.engine.uds` Unix socket.
  - It streams.
  - `probe()` GETs the agent card.
- `remote.py` is `RemoteConnector`. It calls a workload's A2A server on another host with `Authorization: Bearer` on every request: the card fetch, each message, the cancel, and the probe.
  - It never follows a redirect. It ignores the card's own URL and pins the JSON-RPC interface to `spec.engine.url`.
  - `spec.engine.protocol: chassis` (the default) reads `chassis.event` as above.
  - `spec.engine.protocol: a2a` is the plain-A2A mode, for a third-party agent (kagent-adk, a managed runtime) that sends no `chassis.event`. The operator picks it. The connector never detects it.
- `plain.py` is the plain-A2A mode. It is chassis-only: `mapping.py` and the workload copy are not touched.
  - `PlainTranslator` builds events from the agent's own stream. It emits `start` itself, once. It ends with one `end` or `error`. It never reads `chassis.event` or `task.history`, so a remote cannot forge a `tool_call` or a chassis error code.
  - Text parts become `delta`. A `last_chunk` artifact event with no `append` is a snapshot, not a delta. It is the `end.output` only if no delta went out. Data, file, and URL parts are ignored. Empty status messages and SSE pings make no event.
  - `COMPLETED` is `metrics` then `end ok`. A unary `task` and a `message` reply are read the same way.
  - `FAILED` and `REJECTED` are `error {code: "a2a.failed"}` with fixed text. The remote's own text goes to the log only, redacted and capped at 300 characters (suggested).
  - `INPUT_REQUIRED` and `AUTH_REQUIRED` are `error {code: "a2a.unsupported_state"}`. The task stays open on the remote, so the connector sends `CancelTask`. The task id is read from a `task`, a `status_update`, or an `artifact_update`.
  - Usage is read from `spec.engine.a2a.usage_key` only, on the task, status, artifact, and artifact-event metadata. The last value wins; values are never summed. Only non-negative integers count. A float with no fractional part counts as an integer. A boolean, a negative, a fraction, a string, or a value above 2^53 (suggested) counts as zero and is logged once per run. Zeros mean unknown. The run's span says `a2a.usage_known`.
  - `plain_message` sends a text part, a data part only when `input.data` is not empty, and no metadata. `chassis.ctx` is not sent. `context_id: omit` (the default) sends no `context_id`. `trace_id` sends the run's trace id.
  - In plain mode a timeout or a transport failure has fixed text (`the remote timed out`, `the remote failed`). The SDK puts the remote's own payload in its exception text. That text goes to the log, redacted and capped, and nowhere else. A stream the SDK cannot parse is `a2a.transport` too. In chassis mode the exception text is still the message (not changed).
  - A failure before the first stream item (a connect error, a timeout) is a lone `error` with no `start`, the same as the rule for a failure before `handle`. The translator emits `start` only when the first item arrives.
  - The span attribute `a2a.task_id` keeps at most 128 characters (suggested) of the remote's task id. At most 64 artifact snapshots are kept (suggested), and none once a delta went out.
  - Not visible: `tool_call`, and the `retry` and `fallback` statuses.
  - Tests: `tests/test_a2a_plain.py` (the translator), `tests/test_remote_connector_plain.py` (the connector against the stub in `tests/plain_a2a_stub.py`), and `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_plain_a2a_contract.py` (`EngineConnectorContract`).
- `packages/workload-a2a` holds the workload-side copies. Its `mapping.py` is byte for byte. Its `server.py` is mirrored.

### mcp

`mcp/` is FastMCP 4, imported only here.

- `server.py` serves `ToolPort`.
  - `build_mcp_server` adds one `PortTool` per definition, with the definition's own schema.
  - Each call gets one `chassis.tool.call` span. The span holds `tool`, the parsed `trace_id`, and `request_id` when the run is in flight. Nothing is charged.
  - It counts `chassis.tool_calls` per tool.
  - A `ToolError` becomes an MCP tool error.
- `agent.py` serves the agent as one MCP tool.
  - `build_agent_mcp` runs `FastMCP.from_openapi` over the `/v1/run` operation's spec.
  - Route maps keep only `POST /v1/run`.
  - The tool is named after the agent.
  - The inner call goes over `httpx2.ASGITransport(app)`.
  - `_run_headers` keeps only `FORWARDED_HEADERS` (`traceparent`, `tracestate`, `Idempotency-Key`). It sets `x-chassis-interface: mcp`.

### openai_compat and anthropic_compat

- `openai_compat/` is the OpenAI chat wire format, not a client.
  - `types.py` holds the SDK types, plus `OpenAIError` with `retryable`.
  - `inbound.py` is `OpenAIInbound`.
  - `messages.py` has `UnsupportedMessage`, `is_empty`, `refuse_extra`, `text_of`, `tool_calls_of`, and `to_port_message`. The model proxy and the OpenAI interface share it.
- `anthropic_compat/` is the Anthropic Messages format.
  - `types.py` holds the anthropic 1.11 types.
  - `inbound.py` is `AnthropicInbound`.

## server

`chassis/server/` is the HTTP side.

### config.py

- `ChassisConfig` and `load_config`.
- `spec.interfaces` is `{openai, anthropic, mcp}`. Each is on by default. Unknown keys are refused.
- `spec.limits` is `LimitsSpec`: `max_tokens_max`, `timeout_ms_max`, `messages_max`, `body_bytes_max`. Each is at least 1.
  - PoC-8 adds the rate limit: `rate_limit` (`true`), `requests_per_minute` (`600`), `request_burst` (`100`), `rate_limit_on_unavailable` (`fail_open` or `fail_closed`). All reload with `spec.limits`.
- `spec.observability` (PoC-7: `session_header`) gains `log_level` (`info`), `metrics_scope` (unset: `/metrics` needs no token), and `prices` (USD per million tokens per route, `ModelPrice`). Restart-only.
- `spec.guardrails` (PoC-7: the three stages, `on_unavailable`) gains `stream_secrets` (`true`) and `output_url_allow` (`[]`, default deny; `*` turns it off). Restart-only.
- `spec.idempotency` is `IdempotencySpec`: `enabled`, `ttl_s`, `lease_s`, `wait_poll_ms`, `max_entry_bytes`.
- `spec.events` is `EventsSpec` with `result_events`. A non-null `consume` is refused at load until event-triggered runs exist.
- `spec.engine.protocol` is `chassis` (the default) or `a2a`. `a2a` is for `connector: remote` only. `spec.engine.a2a` is `{usage_key: null, context_id: omit | trace_id}`. It needs `protocol: a2a`. Both are restart-only. `as_mapping()` leaves them out for `sidecar` and `inprocess`. suggested: the names and defaults.
- `RELOADABLE` lists the field paths a reload may change. Every other path is restart-only.
- `spec.idempotency.ttl_s` is at most `TTL_S_MAX` (30 days) and `lease_s` at most `LEASE_S_MAX` (one hour). Neither may be `.inf` or `.nan`. suggested: both caps.
- suggested: every default.

### config_loader.py

- `ConfigReloader` reads the bootstrap file first.
- With a real config adapter, it then reads the store document `agents/<agent.name>.yaml`.
- `start` fails on an invalid document. The error names field paths only.
- `on_change` swaps `state.config` in one assignment. Or it refuses with `invalid` or `restart_required` and keeps the last good config.
- Metrics: `chassis.config.reloaded` and `chassis.config.rejected{reason}`.
- A store document's version is `<version>+<hash12>`, or `<hash12>`.
- A document the hash cannot read (mixed key types, nesting too deep) is `invalid`.
- The pipeline's `Idempotency` takes the new `spec.idempotency` through `Idempotency.reload`.
- A port that polls (`PollingConfig` in `ports/config.py`) gets an `on_poll_failed` hook that counts `chassis.config.poll_failed`.

### idempotency.py

- `Idempotency.begin` returns `Claim`, `Replay`, or `Refusal`.
- The claim is a lease, renewed every `lease_s / 3`.
- Without a good renew, the claim is fenced on the replica's clock at `FENCE_FRACTION` 0.8 of `lease_s`.
- A lost claim runs `Claim.on_lost` and never writes.
- A takeover after a wait gets `Claim.timeout_ms`, the budget left. It is at least `MIN_RUN_MS` 500.
- `Claim.finish` caches only a run that ended with `end`.
  - Over `max_entry_bytes`, it stores a `too_large` marker.
  - Otherwise it frees the key.
- An entry of another version is a final 409. So is a `too_large` marker.
- `store_key` is `chassis:idem:v1:<agent>:<sha256>`.
- `fingerprint` covers `{agent, input, context_ref}`.

### readiness.py

- `ReadinessMonitor`.
- `ProbeSettings(interval_s=2.0, timeout_s=1.0, failures=3)`.
- `not_ready_reason` is one of `starting`, `draining`, `workload_unreachable`.

### lifecycle.py

- `QuietServer` leaves signals alone.
- `CloseWhenDraining` is a pure ASGI middleware on the public app only.
  - While `draining`, it adds `Connection: close` to every response.
  - So the drain delay must exceed endpoint removal plus the clients' idle keep-alive window.
- `Drain` runs the shutdown.
  - SIGTERM sets `draining`.
  - It sleeps the delay, then closes the public listener.
  - It waits for in-flight runs, then closes the proxy listener last.
  - A second signal forces exit.

### results.py

- `ResultPublisher` hooks `pipeline.on_finished` when `spec.events.result_events` is on.
- The publish runs in the background. The request never awaits it.
- `id` is `event_id(request_id, type)`, a uuid5.
- The key fields hold `key_hash(key)`, never the raw key.
- Metrics: `chassis.events.published`, `.publish_failed`, `.publish_abandoned`.
- Shutdown waits up to `SHUTDOWN_WAIT_S` 5.

### app.py

- The public app serves `/health`, `/ready` (with `reason`), and `/manifest`. Then `mount_interfaces` runs last.
- Each run holds a `RunRecord` in `app.state.runs`.
- The lifespan builds the ports. It starts the reloader and the readiness monitor. It adds the result publisher.
- `create_app(..., probe=)`.

### manifest.py

- `build_manifest`, `mount_manifest`, `spec_sha256`.
- `GET /manifest` is built per request from the config, the OpenAPI spec, and the agent tool.

### pipeline.py

`RunPipeline` lives at `app.state.pipeline`. It is the run every inbound adapter shares.

- `served` and `to_request`.
- `open(request, *, interface="native")` raises `TraceIdInUse`, which is counted and logged.
- `Run.events()` runs in one `chassis.run` span. An engine exception becomes one `engine_error` event.
- `chassis.requests` and the span are labeled `interface`.
- `Run.response()`, `Run.complete()`, and an idempotent `Run.close()`.
- `RunStream` takes optional `headers` and `on_close`. It frees the trace id even when the body is never iterated.
- `Run.seen` and `Run.done`.
- `on_finished` holds async callbacks. Each is awaited once after each run, never for a replay.

### interfaces/

The public interfaces over the pipeline.

- `mount_interfaces(app, config)` installs the body cap and the validation format.
  - It always mounts native.
  - Then it mounts the ones `spec.interfaces` leaves on, MCP last.
- `ids.py`
  - `resolve_ids` reads the body first, then `traceparent` and the `Idempotency-Key` header. Otherwise it mints ids.
  - `key_from` is `header`, `body`, or `minted`. Only a sent key reaches idempotency.
  - `may_remint`.
  - `open_run` re-mints a trace id in use once and counts `chassis.trace_id_reminted{interface}`.
  - The exception is a native body `trace_id`. It keeps its 409.
- `hold.py` has `hold` and `Held`, the hold rule.
  - The run is read in a task of its own, so its span stays in one context.
- `serve.py` has `serve`, one call over an `InboundAdapter`.
  - `enforce_limits` runs after `to_request`.
  - Then `Idempotency.begin` runs before `open_run`.
  - A refusal goes through `adapter.error`.
  - A lost claim cancels the run (`_fenced`). It answers 409 `idempotency_in_progress` with no `on_finished`.
  - A replay answers with `Idempotent-Replayed: true` (`REPLAYED_HEADER`) and no run.
  - In complete mode, a watcher polls `disconnected` every `DISCONNECT_POLL_S` 0.25. It cancels the run of a client that left.
  - Metric: `chassis.inbound_ignored{interface, param}`.
  - A chassis failure after the run opened closes the run and logs it. It answers 500 `internal_error` with `x-should-retry: false`.
  - `SSE_EVENT_SCHEMA`.
- `errors.py`
  - `INTERFACE_KEY` is `x-chassis-interface`.
  - `register_validation_format`: a `RequestValidationError` is 400 in the route's format. Native keeps 422.
- `limits.py`
  - `enforce_limits` answers 400 `limit_exceeded` over a `spec.limits` ceiling. It refuses, never clamps.
  - `BodyLimit` is a pure ASGI middleware. It answers 413 over the live `body_bytes_max` before parsing.
- `native.py` has the strict `RunRequest`, `NativeInbound`, and `describe_run`.
  - `POST /v1/run` has `operation_id` `run`. It declares 400, 409, 413, and 503.
- `openai.py` is the OpenAI interface (public port).
  - `operation_id` is `chat_completions`.
  - `model` is the agent's name.
- `anthropic.py` is `POST /v1/messages`, `operation_id` `messages`.
- `mcp.py` is `/v1/mcp` on the public port, the agent as a tool.
  - `run_operation_spec` and `mount_agent_mcp`.
  - Streamable HTTP is built in the public app's lifespan. It answers 503 before and after.

### proxy_app.py

- `create_proxy_app(public_app)` builds the localhost-only proxy app.
- It shares the public app's `state` and has no lifespan of its own.
- It mounts `ports.events.inbound_routes()` when the events adapter has them.

### model_proxy.py

The model proxy (proxy port) is the OpenAI-compatible pass-through a workload calls.

- `model` is the LiteLLM route.
- It maps onto `ports.model` with `adapters/openai_compat/messages`.
- It never forwards `Authorization`.
- It is keyed to its run by `traceparent`.
- It opens one `chassis.model.call` span per call.
- A correlated call's `max_tokens` is capped at the run's remainder. When unset, it is the remainder.
- The tokens are reserved at start and settled on usage.
- When nothing is left, it answers 429 `budget_exhausted`. That is `retryable` only when in-flight reservations hold the rest.
- Uncorrelated calls are served with their own `max_tokens` and counted.
- `admit(...)` is the one admission step both routes use: count the call, find the run, refuse with 429, else reserve.
- An upstream 401 or 403 answers 403 `model_route_denied` on both routes (it was 500 `http_401`). It counts `chassis.model_upstream_denied{route}` and logs once. A chat stream keeps its 200 and its error frame.

### model_proxy_messages.py

The Anthropic proxy route (`POST /v1/messages` on the proxy port; contract v5, part A) is a second wire format on the model proxy. It is not the Anthropic interface on the public port, which calls the agent.

- Both proxy listeners serve it, because both mount the one model proxy router. On the remote listener it is behind `BearerAuth` and `RequireRun`.
- The reader is `anthropic_compat/model_wire.py`. It reads the raw JSON by hand and maps it onto `ModelMessage`, `ToolSpec`, and `max_tokens`. The SDK types cannot hold what the Claude CLI sends.
- It reads one header, `traceparent`. It never reads or forwards `authorization`, `x-api-key`, `anthropic-version`, or `anthropic-beta`. The query is ignored.
- The body is capped at 4 MiB (`BODY_CAP_BYTES`, suggested), counted while it is read and before it is parsed. Over it, 413 `request_too_large`.
- A streamed answer holds for `FIRST_CHUNK_WAIT_S` (5 s, suggested) so a model error in that window keeps its HTTP status. After that an error is one `event: error` frame. A `ping` goes out every `PING_INTERVAL_S` (15 s, suggested).
- A correlated stream whose client leaves after the model call started is charged its last known usage, or the whole reservation. The chat route does not do this (Known gap 004 G-2).
- A stream has a wall clock: the rest of the run's `budget.timeout_ms`, else 300 s (suggested). At expiry it sends an `event: error` frame (`model_timeout`), closes the upstream stream, and is charged its whole reservation.
- `model` is capped at 256 characters (suggested). Deeply nested JSON is a 400.
- Every error is Anthropic's shape with a `request-id` header and `x-should-retry`. Model errors send fixed text. The upstream text of a rejected request is logged at the chassis, never sent.
- Empty assistant turns and empty `system` entries are skipped. An empty user turn is a 400. A JSON boolean for `max_tokens` or `temperature` is a 400.
- `/v1/messages/count_tokens` is a 404 in the Anthropic shape. On the remote listener its 401 keeps the v4 body, because the middlewares test the exact path `/v1/messages`.
- Counters: `chassis.model_proxy.ignored{format, param}` for each dropped field. The `chassis.model.call` span has `format`.
- The remote listener's route list is pinned to `/v1/chat/completions`, `/v1/messages`, `/mcp`.

### correlation.py

- `RunRegistry` holds at most one in-flight run per trace id. A second gets `TraceIdInUse`.
- `RunRecord` has `reserve`, `settle`, and `release`.
- It re-exports `parse_traceparent`.

### cli.py

`chassis serve` runs both servers through `lifecycle.Drain`.

- The public listener is on `--port` 8080. suggested.
- The proxy is on `--proxy-host 127.0.0.1 --proxy-port 8090`. suggested.
- A non-loopback proxy host is refused unless `--allow-any-proxy-host`. That flag is for tests; it is not the `remote` lane.
- The `remote` lane adds a third listener: `--remote-proxy-host <pod IP>` and `--remote-proxy-port` (8091, suggested). See [remote_auth.py](#remote_authpy).
- The proxy listeners bind first. A taken proxy port exits with status 3 before the public listener binds.
- `--drain-delay-s` 5 and `--drain-timeout-s` 30. suggested.

### tool_endpoint.py

- `mount_tool_endpoint(proxy, lifespan_app=public_app)` mounts `/mcp` on the proxy port. It serves the tools for workloads.
- It is never on the public port.
- Streamable HTTP is built from `state.ports.tools` in the public app's lifespan. It answers 503 before that.

## PoC-5 modules

Spec: [contract v4](../contracts/contract-v4.md). How it works: [the PoC-5 guide](poc-05-how-it-works.md). The `remote` connector itself is under [a2a](#a2a).

### remote_auth.py

`chassis/server/remote_auth.py` is the remote proxy listener. It serves the model proxy and the tool endpoint on the pod IP, for a workload in another pod.

- `create_remote_proxy_app(public_app, tokens)` builds its own app from a fixed route list: both model proxy routes and `/mcp`. Every other path is 404. There are no docs and no OpenAPI document.
- It shares `public_app.state` and has no lifespan of its own. Build it before the public app starts.
- Two ASGI middlewares wrap it, outermost first. Both refuse every non-`http` scope except `lifespan`.
  - `BearerAuth(tokens)`: `Authorization: Bearer <t>` must equal the current or the previous token. It compares with `hmac.compare_digest`. Else 401 `remote_unauthenticated`, one fixed body (the Anthropic shape on `/v1/messages`). It counts `chassis.remote.auth_failed{reason}` and removes the header before the route sees it.
  - `RequireRun`: the `traceparent` must name a run in flight. Else 403 `run_required`. So a remote spends tokens and calls tools only inside a run, under its budget.
- `remote_tokens(auth)` reads the tokens from `spec.engine.auth.token_env` and `previous_token_env`. A missing variable is a `LookupError` that names it.
- `chassis serve` runs it only when `spec.engine.connector` is `remote` (see [cli.py](#clipy)).
- Tests: `tests/test_remote_auth.py`, `tests/test_server_cli.py`. Scenarios: `pocs/poc-05-sandboxed/tests/test_poc05_remote_lane_offline.py`; on kind, `test_poc05_kind_remote_controls.py` (`POC05_KIND=1`).

### mcp/gateway.py

`chassis/adapters/mcp/gateway.py` is `McpGatewayTools`, the real `ToolPort` over LiteLLM's MCP gateway. `REGISTRY` names it as `tools: mcp`.

- It authenticates with the service's own LiteLLM virtual key. The gateway's per-key allow-list is the hard limit; the adapter adds none of its own.
- The httpx client ignores the environment (`trust_env=False`) and never follows a redirect. The key is never logged, never in an error, and never in `repr`.
- `list_tools()` is sync, so the adapter keeps a cached list. `await refresh()` fills it. `start()` refreshes it in the background. A failed refresh keeps the last list.
- The tool endpoint's lifespan calls `refresh()`, then `start()`, and `aclose()` on shutdown.
- `call()` checks before any request: a write tool with no key raises `idempotency_key_required`; arguments that fail the schema raise `bad_arguments`.
- Gateway answers map to `unknown_tool`, `tool_denied`, `bad_arguments`, or `tool_unavailable` (see [ports](#ports)). A `ToolError.message` is fixed text, never an upstream body.
- Tests: `tests/test_tool_gateway_contract.py` (the whole `ToolPortContract`, over the fake MCP server in process), `tests/test_tool_endpoint_gateway_lifecycle.py`. On kind: `pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py`.

### Write mode

- A write tool has `read_only: false`. `ToolPort.call` requires an `idempotency_key` for it.
- The tool endpoint derives one key per write call inside a run (`run_key_of`, then `adapters/mcp/server.tool_key`). A write with no run is refused with `idempotency_key_required`.
- The fake tools follow the same rule and run a keyed write at most once.
- Tests: `tests/test_tool_idempotency.py`.

### The uncorrelated cap

- A model call that names no run in flight is served, but a replica spends at most `spec.limits.uncorrelated_tokens_per_minute` tokens (20000, suggested) of them per fixed one-minute window.
- Past the cap, 429 `budget_exhausted`. `0` refuses every such call. It is reloadable. A correlated call never touches it.
- It lives in `server/model_proxy.py` (`UncorrelatedCap`) and covers both model proxy routes.
- Tests: `tests/test_uncorrelated_cap.py`.

### spec.trust

- `spec.trust` is `trusted` or `untrusted` (`server/config.py`). `untrusted` needs `spec.engine.connector: remote`.
- It defaults to `trusted` in `fake` and `local`. The `cloud` profile must name it. It is restart-only.
- The manifest shows it as `ManifestAgent.trust`.
- Tests: `pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py`, `tests/test_manifest.py`.

### Remote lane tests

- The connector: `tests/test_remote_connector.py`, `tests/test_remote_token_scrub.py` (the remote's token never leaves the connector).
- The lane contract: `pocs/poc-05-sandboxed/tests/test_poc05_remote_lane_contract.py`.
- Every PoC-5 scenario is in `pocs/poc-05-sandboxed/tests/`. The kind ones run with `POC05_KIND=1`.

## PoC-6 modules

Spec: [contract v5](../contracts/contract-v5.md). How it works: [the PoC-6 guide](poc-06-how-it-works.md).

- The plain-A2A mode of the `remote` lane: `spec.engine.protocol: a2a`, for a third-party agent that sends no `chassis.event`. The module is `adapters/a2a/plain.py`; see [a2a](#a2a). The config fields are under [config.py](#configpy).
  - It is chassis-only: never edit `mapping.py` for it.
  - It cannot show `tool_call`. The remote's own error text never reaches a `Response`.
  - Tests: `tests/test_a2a_plain.py`, `tests/test_remote_connector_plain.py`, `tests/test_config_plain_a2a.py`, and `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_plain_a2a_contract.py`.
- The Anthropic proxy route (`POST /v1/messages` on the proxy port): see [model_proxy_messages.py](#model_proxy_messagespy).
- A refused model route answers 403 `model_route_denied` on both model proxy routes: see [model_proxy.py](#model_proxypy).
- The bake-off scenarios are in `pocs/poc-06a-bake-off-sidecar-lane/tests/`, `pocs/poc-06b-bake-off-remote-lane/tests/`, and `pocs/poc-06c-pretrained-slm/tests/`.

## PoC-7 modules

Spec: [contract v6](../contracts/contract-v6.md). Decisions: ADR-007 to ADR-010 in `docs/planning/adr/`. Every port is off (`none`) in the `fake` and `local` profiles until an agent names it.

| Module | What it does | Contract v6 |
| ------ | ------------ | ----------- |
| `chassis/ports/{auth,guardrail,evaluator,feedback}.py` | The four new ports, each with a no-op default in `PortBundle` | A.1 to A.4 |
| `chassis/fakes/{auth,guardrail,evaluator,feedback}.py` | `StaticTokens`, `KeywordGuardrails`, `ScriptedEvaluator`, `InMemoryFeedback` | A.1 to A.4 |
| `chassis/adapters/otel` | `OtelTelemetry`: OTLP over HTTP, the run span on the request's W3C trace id, `user.id` and `session.id` as baggage | A.5, F |
| `chassis/adapters/oidc` | `OidcAuth`: a JWT checked against the IdP's JWKS (pyjwt) | A.1, D.1 |
| `chassis/adapters/litellm/guardrails.py` | `LiteLLMGuardrails`: LiteLLM's `POST /guardrails/apply_guardrail`, guardrail names per stage | A.2, D.3 |
| `chassis/adapters/langfuse` | `LangfuseFeedback`: Langfuse `POST /api/public/scores`, plain httpx | A.4 |
| `chassis/adapters/evaluators.py` | `JudgeEvaluator` (an LLM judge through `ModelPort`) and `EncoderEvaluator` (a TEI classifier) | A.3 |
| `chassis/server/security.py` | `CallerAuth`: the bearer token and the route's scope on the public app | D.1, D.4 |
| `chassis/server/guard.py` | The `input`, `tool_output`, and `output` guard stages | D.2 |
| `chassis/server/marking.py` | `MarkOutputs`: `X-AI-Generated` and `X-AI-Disclosure-Required` on every run interface | E |
| `chassis/server/feedback.py` | `POST /v1/feedback`, the request-to-trace lookup in `StatePort`, the event `agents.feedback.received.v1` | B, C |
| `chassis/server/evaluator_gate.py` | `GatedEngine`: sync and async gating, retry, fallback, sample rate | G |
| `chassis/redaction.py` | Regex redaction of the chassis's logs and events. Never a named-entity model | B.6, ADR-008 item 6 |
| `chassis/core/trace.py` (`continued`, `parent_span_id`) | A checked inbound `traceparent` for the model proxy and the tool endpoint to continue | A.5 |

- The model proxy also sends every call of the gate's fallback attempt to the fallback route (`chassis.model_route_forced`).
- Tests: the fake bindings are in `tests/test_contracts_poc07.py`; each real adapter binds the same suite in its own file (`test_oidc_adapter.py`, `test_litellm_guardrails.py`, `test_evaluators.py`, `test_langfuse_feedback.py`, `test_otel_telemetry.py`), offline against a stub. The scenarios are in `pocs/poc-07-cross-cutting-decisions/tests/`.

## PoC-8 modules

Spec: [contract v7](../contracts/contract-v7.md). Decisions: ADR-007 and ADR-008 in `docs/planning/adr/`. No new port.

| Module | What it does | Contract v7 |
| ------ | ------------ | ----------- |
| `chassis/metrics.py` | `MetricsRegistry`: counters and span histograms in memory, the Prometheus text format, the declared families. Both telemetry adapters hold one at `.metrics` | A.3 |
| `chassis/logs.py` | `configure_logging`, `JsonFormatter`, `run_context`: one redacted JSON line with `request_id` and `trace_id` | B |
| `chassis/dashboards/` | `python -m chassis.dashboards`: one Grafana dashboard per agent in `agents.txt`, written to `deploy/dashboards/` | B |
| `chassis/server/observability.py` | `GET /metrics` and `OpenMetrics` (open when no `metrics_scope`); `count_usage` for tokens and cost | A |
| `chassis/server/rate_limit.py` | The per-caller token bucket in `StatePort`, 429 `rate_limited` with `Retry-After` | C |
| `chassis/server/stream_guard.py` | The stream secret check and URL masking on every streamed run | D.1 |
| `chassis/server/output_filter.py` | `find_secret`, `mask_urls`, `mask_answer`: the secret patterns and the URL allow-list | D |
| `chassis/adapters/ai_marking.py` | The `ai_marking` body field on the OpenAI and Anthropic formats | E.1 |

- The model proxy opens `chassis.model.route` per admitted call and counts tokens and cost (B, A.3).
- `server/marking.py` adds `Access-Control-Expose-Headers`; `server/results.py` sets the `aigenerated` CloudEvent extension (E.2, E.3).
- `chassis serve` calls `configure_logging` and gives uvicorn `log_config=None`, so every line is JSON.
- Tests: `test_metrics.py`, `test_json_logs.py`, `test_router_span.py`, `test_dashboards.py`, `test_collector_log_redaction.py`, `test_rate_limit.py`, `test_output_filter.py`, `test_marking_body.py`. The red-team controls: `packages/contract-suites/tests/test_redteam_controls.py`. The scenarios are in `pocs/poc-08-cross-cutting-build/tests/`.

## profiles.py

- It holds `spec.adapters`, the `fake`/`local`/`cloud` profiles, `build_ports`, and `check_lane`.
- The lane is `spec.engine.connector`. The default is `sidecar`.
- `spec.adapters.engine` is refused.
- `connector: inprocess` builds `InProcessConnector`. `spec.engine.handle` names the workload as `module:attribute`.
- `connector: sidecar` builds `SidecarConnector`. It uses `spec.engine.url` and the optional `spec.engine.uds`.
- `connector: remote` builds `RemoteConnector`. It adds `spec.engine.auth`, and `spec.engine.protocol: a2a` for a third-party agent.
- `spec.adapters` (`model`, `config`, `telemetry`, `tools`, `state`, `events`) merges over the profile defaults per field.
- `cloud` refuses `fake` and `memory` adapters. suggested.
- `state` is `memory` or `valkey`. The default is `memory` in `fake`, and `valkey` in `local` and `cloud`.
- `events` is `none`, `memory`, `kafka`, or `dapr`. The default is `none` everywhere.
- `config` adds `minio` and `s3`.
- Every PoC-4 `REGISTRY` entry is a `lazy` factory. It imports its adapter inside the function. So an unused SDK (valkey, aiokafka, minio) is never loaded.

## configs

- `fake.yaml` runs the simplifier `echo_python:handle` in the `inprocess` lane, with a scripted model.
- `sidecar.yaml` is `local` in the `sidecar` lane at `http://127.0.0.1:9000`.
- `local.yaml` and `sidecar.yaml` both pin `config: memory` and `state: memory`. So the PoC-1 to PoC-3 stacks need no store.
- `scale.yaml` is the Compose scale stack: `config: minio`, `state: valkey`, `events: none`.
- `scale-kafka.yaml` and `scale-dapr.yaml` are the same, plus `result_events: true`.
- `scale-inprocess.yaml` runs `echo_python` in the chassis, for the hop measurement.

## schemas

- `schemas.py` and `schemas/*.json` are the published JSON Schemas.
- v0: `request`, `response`, `context`, `task_input`, `events`, `manifest`, `chassis-config`.
- v1: `task-result`, the result-event payload.
- `make schemas` generates them. A test fails on drift.

## Tests in detail

- Contract bindings: `tests/test_contracts.py` (ports) and `tests/test_inbound_contract.py` (`InboundAdapterContract`, three times).
- The interface matrix is in `pocs/poc-03-one-interface-every-client/tests/`.
- PoC-4 bindings are files of their own: `tests/test_state_contract.py` (`InMemoryState`) and `tests/test_events_contract.py` (`InMemoryBus`).
- PoC-4 unit tests: `test_idempotency.py`, `test_idempotency_serve.py`, `test_client_disconnect.py`, `test_config_loader.py`, `test_readiness.py`, `test_shutdown.py`, `test_result_events.py`, `test_dapr_inbound.py`, `test_events.py`.
- Real adapters (Valkey, MinIO, Kafka, Dapr) bind the same suites in `tests/integration/`.
  - All are marked `network`.
  - `make test-integration` runs them, with Docker and testcontainers.
  - They are skipped when Docker is not reachable. They are never in the gate.
- The PoC-4 scenarios are in `pocs/poc-04-stateless-scalable/tests/`.
