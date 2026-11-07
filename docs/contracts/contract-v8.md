# Contract v8

The written contract after the core review of 2026-10-29. The user approved one set of its proposals, and they land here as one step:
- R3-02, R1-08, and the rest of R2-08;
- R3-14;
- R1-09 and the cap part of R4-27;
- R2-02;
- R2-11;
- R3-30;
- R2-10 and R7-16;
- R4-10;
- the R6 note on uncorrelated routes.

Contract v8 supersedes [contract v7](contract-v7.md) for what it names. v7, and v6 to v1 before it, still hold for everything this document does not name.

v8 is **not purely additive**. It narrows the ids and the event fields, changes the error text a native or MCP caller sees, stops reading one header, and adds refusals:
- a remote call with the wrong run secret;
- a remote call with more than one `traceparent` header;
- too many duplicates waiting on one key;
- feedback from a caller who does not own the run.

Status: **draft, 2026-11-06.** Written against the worktree on top of `0a2e4df`, before commit. Where this text and the code differ, the code wins. Evidence is a test name or a command. A choice only the user can make is marked `OPEN (user):`. The decision record is [ADR-011](../planning/adr/011-per-run-secret-and-v0-wire-bounds.md). It extends [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md) decision 2 and closes one open question of [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md).

Contract v8 is a version of this document, not of the wire:
- The event schema stays `schema_version: "0"`.
- `SUPPORTED_SCHEMA_VERSIONS` stays `("0",)`.
- No `events.v1.json` is made.

Why the narrowing needs no bump is in C.3.

## Decisions at a glance

| | Change | Contract it touches | `inprocess` | `sidecar` | `remote` |
| - | ------ | ------------------- | ----------- | --------- | -------- |
| A | Native and MCP send the fixed error text | The native and MCP reply. Not the event schema | One copy per error | Same | Same |
| B | One `materialize`, one location format | The OpenAI and Anthropic interfaces' 400 text | None | None | None |
| C | Limits on the ids and the event fields | The envelope, `Context`, the event schema (still v0) | A pydantic check per event | Same | Same. A hostile remote's out-of-bound values become `a2a.bad_event` |
| D | A per-run secret on the remote listener | `Context` (+1 optional field), the proxy wire (+1 header), `RequireRun` | Minted and sent, never checked | Minted and sent, never checked (the loopback proxy has no `RequireRun`) | One constant-time compare per proxy call. Every remote workload sends the header |
| E | A cap on one run's lane stream | A new error code. The A2A connector | Counted | Counted | Counted. Stops a memory flood from a remote |
| F | The MCP hop is a scope flag, not a header | `x-chassis-interface` stops counting on `/v1/run` | None | None | None |
| G | Feedback belongs to the run's caller. The metric label is bounded | `/v1/feedback` answers. A field in the `StatePort` lookup | +1 field per lookup | Same | Same |
| H | A cap on the waiters of one in-flight key | `/v1/run` keyed calls: a new 409 case | A counter per replica | Same | Same |
| I | An uncorrelated model call goes to the default route | Model proxy routing | The loopback proxy | The loopback proxy | No effect: `RequireRun` already refuses a call with no run |

## What did not change

- **`schema_version` and the event set.** Still `"0"`, still the six events, still `extra="forbid"`. No field is added to or removed from an event.
- **`handle`'s signature, `TaskInput`, the A2A mapping in chassis mode, and the frozen error codes with their `retryable`.** These are contract v5 C.2 items 1, 2, 6, and 7. `task_input.v0.json` is byte-identical (`6d8aa949b462c77e`).
- **The collected `Response`, the result events, the idempotency cache, the span, and the log.** They keep the run's own error text, redacted (A).
- **The ports.** No port changed. No new port. No new `spec.*` field. Every new value is a module constant marked `suggested:`.
- **The status table** (`status_for`). The two new refusals use codes and statuses that already exist: 400 `invalid_body` and 409 `idempotency_in_progress`. The new code `a2a.stream_too_large` takes 502 from the existing "not retryable" row.
- **The 403 `run_required` body bytes.** They are the same as in v4 and v5 A.8, on both body shapes.

---

## A. The fixed error text on native and MCP (R3-02, R1-08, R2-08)

- A run error on `POST /v1/run` is still a 200 envelope with `status: error`. Its `output.error.message` is now `public_message(code)`, never the run's own text. That text can hold internal URLs or an upstream body. The same applies to a stream's `event: error` frame and to its last `response` frame.
- The MCP interface (`/v1/mcp`) calls `/v1/run` in process, so its envelope carries the same text.
- `public_message(code)` is the `PUBLIC_MESSAGES` text (`chassis.core.inbound`), else `RUN_FAILED` (`the agent run failed`) for a workload's own code. suggested: the text. This was already the OpenAI and Anthropic rule.
- New entry: `a2a.stream_too_large` → `the agent sent more than the stream limit` (E). It is an entries-only diff to `core/inbound.py`, the one exception that contract v5 C.3 allows.
- The mapping happens at the native back-mapping only (`chassis.server.interfaces.native.public_error`). Everything else keeps the raw text, redacted: the collected `Response`, the result events, the idempotency cache, the span (`error.message`), and the `run error` log line. A replay from the cache goes through the same mapping.
- Every writer that builds an error message from exception text cuts it to `ERROR_MESSAGE_MAX` (C.2). So the error event it builds is itself valid. The writers are `pipeline._guarded`, `connector._failure_text`, and `task_store.error_event` (in both copies).

Evidence: `packages/chassis/tests/test_interface_security.py::test_native_stream_and_complete_send_the_public_text_and_log_the_raw_text`, `::test_mcp_tool_call_sends_the_public_text_when_the_workload_raises`, `test_serve_failures.py::test_native_sends_the_public_text_for_an_engine_error_contract_v8`, and `test_pipeline.py::test_an_engine_exception_text_is_cut_to_the_error_message_cap`. The R3-02 strict xfail is removed.

Supersedes: contract v2 on the native `output.error.message` (the raw text was the wire value). Also contract v5, where it repeats that rule.

---

## B. One `materialize` for both chat interfaces (R3-14)

- `chassis.adapters.chat_common.materialize(value, path="")` reads every lazy iterator once and returns plain values. It never changes `value`. It raises `InvalidBody` both for a `ValidationError` raised while reading and for a body nested past Python's stack (`TOO_DEEP_MESSAGE`). So both chat interfaces answer 400 `invalid_body`, never a 500. The OpenAI interface (public port) used to 500 on a body nested too deep.
- One location format, the bracket form `param` already uses: `messages[1].content`. The OpenAI interface used to say `messages.1.content`.
- At most `INVALID_PARTS_MAX` (3, suggested) distinct `<loc>: <msg>` parts, joined with `"; "`, then `"; ..."` when there are more. The input is never echoed.
- `param` keeps each format's rule: the top-level key on OpenAI, none on Anthropic. `anthropic_compat.inbound` re-exports `materialize`, `InvalidBody`, and `TOO_DEEP_MESSAGE`.

Evidence: `packages/chassis/tests/test_chat_materialize.py::test_both_chat_routes_name_a_failing_lazy_item_the_same_way`, `::test_openai_refuses_with_400_when_the_body_nests_past_the_python_stack`, `::test_invalid_body_keeps_at_most_three_distinct_parts`.

---

## C. Limits on the ids and the event fields (R1-09, R4-27)

### C.1 The ids

| Constant (`chassis.core.envelope`) | Value | On |
| ---------------------------------- | ----- | -- |
| `ID_MAX` | 128 (suggested) | |
| `ID_PATTERN` | `^[A-Za-z0-9._:-]{1,128}$` (suggested; the alphabet of `FEEDBACK_ID_PATTERN`) | `request_id`, `trace_id` |
| `KEY_MAX` | 255 (suggested) | |
| `KEY_PATTERN` | `^[\x21-\x7E]{1,255}$`: visible ASCII, no space (suggested) | `idempotency_key` |

- Every model that carries the ids uses the types `Id` and `IdempotencyKey`: `Request`, `Response`, `Context`, and the native `RunRequest`, where they stay optional.
- The key alphabet is wider because clients send keys with `#` and `/`, and the store hashes the key. ASCII only refuses the lone surrogate of R4-27.
- **A native body id** outside its pattern is a FastAPI 422 (`DetailIssues`).
- **A header id** is checked by `chassis.server.interfaces.ids.id_problem` with `re.fullmatch`. A bare `$` would let a trailing newline through. Today this covers the `Idempotency-Key` header; a `traceparent` id is 32 hex by construction. An id that fails is 400 `invalid_body` in the route's format, before the request is built. The text names the field and the rule, never the value: `idempotency_key must be 1 to 255 visible ASCII characters, with no space`. No new code. `resolve_ids` itself never raises.
- An MCP caller's forwarded `Idempotency-Key` takes the same check, so a bad one is an MCP tool error.

Evidence: `packages/chassis/tests/test_envelope.py::test_request_refuses_bad_ids`, `::test_request_accepts_ids_at_the_bounds`, `test_interface_security.py::test_an_idempotency_key_header_with_a_newline_is_400_invalid_body_on_every_interface`, `::test_a_native_body_id_outside_the_pattern_is_422`.

### C.2 The event fields

suggested: every value (`chassis.core.events`). Each is set far above what an honest producer emits.

| Field | Bound | Constant |
| ----- | ----- | -------- |
| `start.request_id` | `ID_PATTERN` | |
| `delta.text` | at most 1,048,576 characters | `EVENT_TEXT_MAX` |
| `tool_call.call_id` | 1 to 256 characters | `TOOL_CALL_ID_MAX` |
| `tool_call.name` | 1 to 128 characters (MCP's own limit; a metric label) | `TOOL_NAME_MAX` |
| `metrics.input_tokens`, `output_tokens`, `latency_ms` | 0 to 2^53 | `COUNT_MAX` |
| `metrics.attempt` | at least 1 | |
| `metrics.cost_usd` | at least 0, finite | |
| `metrics.model_route` | at most 256 characters, as the proxy's `model` cap | `MODEL_ROUTE_MAX` |
| `error.code` | `^[A-Za-z0-9._:-]{1,64}$` (a metric label) | `ERROR_CODE_PATTERN` |
| `error.message` | at most 8192 characters | `ERROR_MESSAGE_MAX` |

- `tool_call.arguments`, `tool_call.result`, and `end.output` have no field bound: a dict's `maxLength` would count keys, not bytes. The stream cap (E) bounds them.
- A remote's event outside a bound is `a2a.bad_event`, with fixed text, and the parse error goes to the log, as for any invalid event. `workload_a2a` validates with its copy of `events.v0.json`, so a template workload's own bad event is `workload.bad_event` before it leaves.
- `workload_a2a`'s `task_store.error_event` cuts its message to its own `ERROR_MESSAGE_MAX` (8192). A test in each package checks the two are equal (`packages/chassis/tests/test_a2a_stream_order.py`, `packages/workload-a2a/tests/test_workload_a2a_stream_order.py::test_task_store_message_cap_equals_the_vendored_schema_cap`; ADR-002: no shared import).

Evidence: `packages/chassis/tests/test_events.py::test_parse_event_refuses_out_of_bound_values`, `::test_parse_event_accepts_values_at_the_bounds`, `test_a2a_stream_cap.py::test_a_remote_negative_token_count_is_a2a_bad_event`, `::test_a_transport_failure_text_is_cut_to_the_error_message_cap`.

### C.3 Why `schema_version` stays `"0"`, and the rule this adds

1. Contract v5 C.3 calls an event change breaking "when an older reader would refuse the stream". Narrowing never does that: an older, looser reader accepts every stream a bounded producer writes.
2. The risk runs the other way: a new reader refuses an old producer's out-of-bound value. The bounds are set so that no conforming producer reaches them. The evidence is contract v5 C.4 step 3: every engine's mapping test parses every event it emits with `parse_event`. Those tests stay green.
3. A bump would protect nothing. The bounds are the receiver's guard. A hostile remote would declare `"0"` to escape a `"1"` that has them, so they must hold for every accepted version. A bump with no new content is the rename that contract v5 C.5 rejected.
4. "The previous major stays accepted" holds trivially: there is no new major.

**The rule added to contract v5 C.3:**
- Tightening a bound so that it refuses only values no conforming producer emits is compatible. It needs no bump.
- The change re-pins the freeze hashes, and the engine mapping tests are its evidence.
- Anything else that narrows a field is breaking, as before.

Decided in [ADR-011](../planning/adr/011-per-run-secret-and-v0-wire-bounds.md), item 2.

### C.4 The schema files and the freeze

`make schemas` regenerated four of the five frozen files. `packages/chassis/tests/test_contract_freeze.py::SCHEMA_SHA256` re-pins them. First 16 hex, as contract v5 C.4 lists them:

| File | At the v5 freeze | Contract v8 |
| ---- | ---------------- | ----------- |
| `events.v0.json` | `60de41563a1d0582` | `292eff45d36fcf36` |
| `context.v0.json` | `d2db0e94a50379f1` | `6561efc893ab0e3e` |
| `request.v0.json` | `4ff8a4324a90e8c0` | `b8221967f78247cd` |
| `response.v0.json` | `63acf4f16dae59f9` | `d643e58e24891029` |
| `task_input.v0.json` | `6d8aa949b462c77e` | unchanged |

`context.v0.json` changes for the id patterns and for `run_secret` (D).

The copies:
- `packages/workload-a2a/src/workload_a2a/schemas/events.v0.json` is copied byte for byte (`test_workload_a2a_copies.py`).
- `packages/workloads/echo-typescript/schemas/events.v0.json` **keeps the old copy.** Its validator (`src/schema.ts`) throws at load on a keyword it does not know: `minLength`, `maxLength`, `pattern`, `minimum`, or `maximum`. TypeScript only produces events and the chassis enforces the bounds, so the old copy is safe. OPEN (user): teach `schema.ts` the five keywords, then copy the file.

Evidence: `uv run python -m pytest packages/chassis/tests/test_contract_freeze.py` passes, inside the run noted under "Contract suites".

---

## D. A per-run secret on the remote listener (R2-02)

### D.1 What travels where

| Step | What | Where |
| ---- | ---- | ----- |
| Mint | `secrets.token_urlsafe(RUN_SECRET_BYTES)`, `RUN_SECRET_BYTES = 32` (suggested; 43 characters) | `chassis.server.pipeline.context_for`, once per run, in every lane |
| Keep | `RunRecord.run_secret`, copied from `ctx.run_secret` by `RunRegistry.open` | One replica, for the life of the run. Never in `StatePort`: a retry is a new run with a new secret |
| To the workload | `Context.run_secret: str \| None` (optional, `repr=False`) | Inside `chassis.ctx`, the path `traceparent` takes. A new optional `Context` field is compatible (contract v5 C.3) |
| Back to the chassis | The header `x-chassis-run-secret` (`remote_auth.RUN_SECRET`), on every model proxy and `/mcp` call, next to `traceparent` | Each workload's HTTP client |
| Check | `RequireRun` on the remote listener | Section D.2 |

- `Context`'s rule changes from "never holds a key" to "never holds a product or platform credential". `run_secret` is a run-scoped capability for the chassis's own listener. It allows only what the run already allows, and only while the run is in flight. So hard requirement 1 holds.
- The rule for `handle` (contract v5 C.2 item 1) gains one clause: `handle` forwards `ctx["run_secret"]`, when it is set, as the `x-chassis-run-secret` header on every model and MCP call. It never logs it.
- **Plain mode** (`spec.engine.protocol: a2a`) gets no secret (`run_secret: None`). A plain remote never receives `ctx`, so it could never send the secret back.

### D.2 The check

`RequireRun`, after `BearerAuth`, on every call to the remote listener: both model proxy routes and `/mcp`.

1. Exactly one `traceparent` header. More than one is refused, never guessed: the reason is `traceparent_count`. `RequireRun` would read one header and FastMCP's `get_http_headers` the other (its dict keeps the last), so the tool endpoint could write under a run the check never saw.
2. The `traceparent` must name a run in flight. Else the reason is `no_run`. This path also runs one `hmac.compare_digest` against a fixed stand-in, so the time does not show whether a trace id names a run.
3. In plain mode (decided by `spec.engine.protocol`, never by the caller), the call passes on the traceparent alone, but only when the run's record holds no secret. It is counted as `chassis.remote.run_secret_skipped{reason="lane_without_secret"}` (suggested). That is a separate counter, so admitted calls never add to the refusal count.
4. Otherwise:
   - **No `x-chassis-run-secret` header:** `secret_missing`.
   - **Wrong, with any of these:** `secret_wrong`.
     - more than one such header;
     - a value longer than `RUN_SECRET_MAX` (128 bytes, suggested; checked before the compare);
     - a record that holds no secret;
     - a value that does not match under `hmac.compare_digest`.
5. Any reason is 403 `run_required` with **the same body bytes for every reason**: `{"error": {"code": "run_required", ...}}`, and the Anthropic body on `/v1/messages` (contract v5, A.8). A caller cannot tell "no such run" from "not your run", so the traceparent is no oracle. Counted as `chassis.remote.run_required{reason}`, where `reason` is `traceparent_count`, `no_run`, `secret_missing`, or `secret_wrong` (suggested). Before v8 the counter had no label.
6. A call that passes has the `x-chassis-run-secret` header removed before the route sees it. So the model proxy and FastMCP can never forward it. The middleware never logs a header.
7. `tool_endpoint.py` does not change. Each stateless `/mcp` POST passes the middleware.

So a remote **process** that serves run A and holds the shared bearer cannot spend run B's budget or send a write under B's run key, as long as it cannot read B's secret.

**What the secret does not isolate.** The secret separates runs only between processes that share no memory and no `/proc`. A remote pod that serves several runs gives its one process every run's `ctx`, so it holds every run's secret. echo-claude-agent puts the secret in the CLI's environment (`ANTHROPIC_CUSTOM_HEADERS`), and any process with the same uid can read it from `/proc/<pid>/environ`. A Bash tool in run A can read run B's secret that way. Full isolation needs one run per pod, which is core-review proposal R6-11 (`docs/reviews/2026-10-29-core-review/proposals.md`). It is not in v8.

Evidence: `packages/chassis/tests/test_remote_auth.py::test_require_run_refuses_run_a_secret_with_run_b_traceparent` (the R2-02 strict xfail is removed), plus these tests in the same file:
- `::test_require_run_refuses_a_missing_secret_with_403`
- `::test_require_run_refuses_two_secret_headers_and_a_wrong_one`
- `::test_require_run_lets_the_right_secret_in_and_the_header_never_reaches_the_route`
- `::test_require_run_lets_a_plain_mode_run_in_without_a_secret`
- `::test_require_run_refuses_a_chassis_mode_run_that_holds_no_secret`
- `::test_require_run_body_bytes_are_the_same_for_no_run_and_wrong_secret`
- `::test_mcp_on_the_remote_listener_refuses_a_wrong_secret`
- `::test_require_run_refuses_two_traceparents_with_the_first_runs_secret` (model and `/mcp`)
- `::test_require_run_compares_a_dummy_secret_when_no_run_is_in_flight`
- `::test_plain_mode_still_checks_the_secret_of_a_run_that_holds_one`

The two-traceparent case also runs through the PoC-5 harness: `pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py::test_h17_two_traceparents_with_the_first_runs_secret_are_403`.

Also `test_pipeline.py::test_context_for_mints_a_fresh_run_secret_for_every_run`, `::test_context_for_gives_a_plain_mode_run_no_secret`, `test_correlation.py::test_open_copies_the_run_secret_and_never_shows_it`, and `test_envelope.py::test_context_run_secret_is_optional_and_hidden_from_repr`.

### D.3 The workloads, and the order of a rollout

- Every built engine sends the header when `ctx["run_secret"]` is set: echo-python, echo-pydanticai, echo-langgraph, echo-openai-agents, echo-smolagents, and echo-typescript, each next to its `traceparent` header.
- echo-claude-agent sends it as a second `ANTHROPIC_CUSTOM_HEADERS` line and in the MCP server's `headers`.
- `workload_a2a` passes `ctx` through unchanged and needs no code.
- The test kits: `poc_harness` `RemoteLane.run_headers(trace)` builds the pair. `chassis_contracts.interface_harness` masks `run_secret` in `PER_RUN_CTX` when it compares contexts.
- **Logs.** The a2a-sdk logs each JSON-RPC body at DEBUG, and the body holds `chassis.ctx.run_secret`. `workload_a2a.logs.configure_logging` pins the `a2a` loggers to WARNING at every level. Evidence: `packages/workload-a2a/tests/test_workload_a2a_logs.py::test_configure_logging_pins_the_a2a_loggers_to_warning_at_debug`.
- **The scrub.** A shell that runs `env` can echo the secret into a delta or a `tool_call`. `RemoteConnector.run` scrubs the run's `ctx.run_secret` from every event it yields, the same way it scrubs the bearer token: each string, key, and list item becomes `[redacted]`. echo-claude-agent's `_scrubbed` removes both values from its own events too, across delta chunks, and from the stderr log line. Evidence: `packages/chassis/tests/test_remote_token_scrub.py::test_chassis_mode_scrubs_the_run_secret_a_remote_echoes`; `packages/workloads/echo-claude-agent/tests/test_echo_claude_agent_handle.py::test_handle_scrubs_the_run_secret_from_every_event_when_bash_prints_the_env`, `::test_scrubbed_removes_the_token_and_the_run_secret_across_delta_chunks`.
- **Rollout:** workloads first, then the chassis. A workload that sends the header to an older chassis is harmless: the older listener ignores it. A chassis-mode remote that does not send it gets 403 `run_required` on every proxy call.

Known limit: a plain-mode remote (kagent-adk) keeps the v5 rule, traceparent alone. Revisit when a plain remote can echo a per-run header (ADR-011).

---

## E. A cap on one run's lane stream (R2-11)

suggested: every value (`chassis.adapters.a2a.connector`).

| Constant | Value |
| -------- | ----- |
| `STREAM_EVENTS_MAX` | 10,000 chassis events per run |
| `STREAM_BYTES_MAX` | 4,194,304 bytes (4 MiB) per run, each stream item's protobuf `ByteSize()`, counted before the item is parsed (plain mode too) |

- Past either cap, the run ends with one `error {code: "a2a.stream_too_large", retryable: false}` with fixed text (`STREAM_TOO_LARGE_TEXT`). The connector stops reading, and the unfinished A2A task is cancelled. An item past the byte cap is never parsed: when the run has no task id yet, it is read from the item itself, so the cancel still goes out.
- The run's span gets `a2a.stream_capped: true`. The counter is `chassis.a2a.stream_capped{lane}` (suggested).
- Every lane reads through the connector, `inprocess` included. So `Run.seen` stays bounded with no second count in the pipeline.

**The new error code:**

| Code | Where | HTTP status (`status_for`) | `retryable` | Public text (suggested) |
| ---- | ----- | -------------------------- | ----------- | ----------------------- |
| `a2a.stream_too_large` | The A2A connector, every lane | 502 on the OpenAI and Anthropic interfaces; a 200 envelope with `status: error` on native and MCP | false | `the agent sent more than the stream limit` |

A new code is compatible (contract v5 C.3). The freeze test asserts no set equality.

Evidence: `packages/chassis/tests/test_a2a_stream_cap.py::test_a_remote_that_streams_past_the_event_cap_ends_with_one_stream_too_large`, `::test_a_remote_that_streams_past_the_byte_cap_ends_with_one_stream_too_large`, `::test_a_first_item_past_the_byte_cap_still_cancels_its_task`, `::test_a_stream_under_both_caps_is_unchanged`.

---

## F. The MCP hop is a scope flag (R3-30)

- The MCP tool's in-process call to `/v1/run` is now marked by the ASGI scope key `chassis.mcp_hop` (`MCP_HOP_SCOPE_KEY` in `chassis.server.interfaces.errors`; suggested: the name), set to `True`.
- `chassis.server.interfaces.mcp` hands `build_agent_mcp` the app wrapped in `_mark_mcp_hop`, which calls it with a copy of each scope that carries the flag. A server never sets a custom scope key from a header, so only that hop can carry it.
- The flag carries trust:
  - `stream` is read as false;
  - the disconnect is not watched;
  - telemetry is labeled `mcp`;
  - a body `trace_id` in use is re-minted, not 409.
- The inner call no longer sends `x-chassis-interface`. A direct caller's `x-chassis-interface: mcp` (or any other value) on `/v1/run` is ignored: the call is native, streams, is watched for a disconnect, and keeps the 409.
- `INTERFACE_KEY` (`x-chassis-interface`) is still the OpenAPI extension on each interface operation, which the manifest reads. It is never read as a request header.

Evidence: `packages/chassis/tests/test_mcp_interface.py::test_a_direct_x_chassis_interface_mcp_header_is_native`, `::test_a_direct_caller_with_the_mcp_header_is_watched_for_a_disconnect`, `::test_the_mcp_tool_call_is_still_labeled_mcp_and_reminted`.

Supersedes: contract v2 and v3, where the header marks the MCP call on the wire.

---

## G. Feedback belongs to the run's caller (R2-10, R7-16)

- The lookup `chassis:feedback:v1:req:<agent>:<sha256(request_id)>` now holds `{"trace_id", "agent", "subject_sha256"}`. `subject_sha256` is the sha256 hex of the run's caller subject (`run.observed.user_id`), or `null` with auth off. The store never holds the subject itself. The key prefix stays `v1`.
- With a stored hash, feedback from a caller whose subject hashes otherwise is 404 `unknown_request`, compared with `hmac.compare_digest`. It is the same answer as "no such run", so the route is no oracle for another caller's runs.
- A stored `null` (the run had auth off) is accepted from any caller. So is a lookup with no `subject_sha256` field, written before this change; it expires within `spec.feedback.lookup_ttl_s`.
- The owner check runs before the `feedback_id` is claimed. So a stranger can no longer claim or overwrite a `feedback_id` on another caller's run.
- **The metric label is bounded.** `chassis.feedback{label}` counts the body's `label` by name only when it is in `FEEDBACK_LABELS` = `{good, bad, wrong, unsafe, slow}` (suggested). An unset label counts as `none` and any other text as `other`. The stored record and the event keep the label as written.

This closes contract v6's open point (B.4) and ADR-009's open question 1: feedback is scoped to the caller whenever auth is on. Still open from ADR-009: a reviewer role that may score any request of the agent.

Evidence: `packages/chassis/tests/test_feedback_route.py::test_feedback_refuses_another_caller_with_404_when_the_run_ended_for_alice` (the R2-10 strict xfail is removed), plus these tests in the same file:
- `::test_the_lookup_holds_a_hash_of_the_subject_never_the_subject`
- `::test_another_caller_cannot_overwrite_a_score_by_its_feedback_id`
- `::test_a_lookup_written_before_the_change_still_accepts_feedback`
- `::test_with_auth_off_any_caller_may_rate`
- `::test_a_run_with_auth_off_accepts_a_caller_with_auth_on`
- `::test_a_free_text_label_is_counted_as_other`

---

## H. A cap on the waiters of one in-flight key (R4-10)

- At most `MAX_WAITERS_PER_KEY` (16, suggested) duplicates of one in-flight keyed call wait for it on one replica (`chassis.server.idempotency`).
- The next one gets 409 `idempotency_in_progress` after one claim try and one read of the store, before it sleeps, with no further poll. That code already exists: `retryable: true`, no `Retry-After`. It is counted as `chassis.idempotency.waiters_full` (suggested).
- This is load shedding. It keeps nothing a retry needs, so exit criterion 8 holds. The bound per cluster is the cap times the replicas. The count is per key, so other keys are not affected.

Evidence: `packages/chassis/tests/test_idempotency_waiters.py::test_the_17th_waiter_on_one_in_flight_key_gets_409_at_once_and_the_store_sees_no_more_polls`, `::test_the_cap_is_per_key_and_another_replica_has_its_own`.

---

## I. An uncorrelated model call goes to the default route (R6 note)

- A model proxy call that names no run in flight (no `traceparent`, or an unknown one) is sent to `spec.model.route`, whatever `model` it names.
- When the named route differs, it is counted as `chassis.model_route_pinned{route}` (suggested). The `chassis.model.route` span has `route.requested` (the name the workload sent), `route` (the default), and `route.forced: true`.
- The rule is in the shared admission step (`chassis.server.model_admission.admit`), so both proxy routes follow it: `/v1/chat/completions` and the Anthropic proxy route `/v1/messages`.
- A correlated call keeps its route, or the run's fallback route (contract v6, G).
- The uncorrelated cap (contract v4) is unchanged.
- The remote listener never sees such a call: `RequireRun` refuses it.

Evidence: `packages/chassis/tests/test_router_span.py::test_an_uncorrelated_call_naming_another_route_is_sent_to_the_default_route`, `::test_a_correlated_call_naming_another_route_keeps_its_route`.

---

## Config reference

No new `spec.*` field. Each new value is a module constant, so a change needs a release. OPEN (user): move the stream caps, the waiter cap, and the feedback labels to `spec.*` fields, through `config.py`.

| Constant | Module | Value |
| -------- | ------ | ----- |
| `ID_MAX`, `ID_PATTERN` | `chassis.core.envelope` | 128, `^[A-Za-z0-9._:-]{1,128}$` |
| `KEY_MAX`, `KEY_PATTERN` | `chassis.core.envelope` | 255, `^[\x21-\x7E]{1,255}$` |
| `EVENT_TEXT_MAX`, `TOOL_CALL_ID_MAX`, `TOOL_NAME_MAX`, `COUNT_MAX`, `MODEL_ROUTE_MAX`, `ERROR_CODE_PATTERN`, `ERROR_MESSAGE_MAX` | `chassis.core.events` | C.2 |
| `RUN_SECRET_BYTES` | `chassis.server.pipeline` | 32 |
| `RUN_SECRET`, `RUN_SECRET_MAX` | `chassis.server.remote_auth` | `x-chassis-run-secret`, 128 |
| `STREAM_EVENTS_MAX`, `STREAM_BYTES_MAX` | `chassis.adapters.a2a.connector` | 10,000, 4,194,304 |
| `MCP_HOP_SCOPE_KEY` | `chassis.server.interfaces.errors` | `chassis.mcp_hop` |
| `INVALID_PARTS_MAX` | `chassis.adapters.chat_common` | 3 |
| `FEEDBACK_LABELS` | `chassis.server.feedback` | `good`, `bad`, `wrong`, `unsafe`, `slow` |
| `MAX_WAITERS_PER_KEY` | `chassis.server.idempotency` | 16 |

suggested: every value in this table.

## New counters and span attributes

| Name | Labels | Section |
| ---- | ------ | ------- |
| `chassis.remote.run_required` | `reason` (`traceparent_count`, `no_run`, `secret_missing`, `secret_wrong`); new label | D |
| `chassis.remote.run_secret_skipped` | `reason` (`lane_without_secret`) | D |
| `chassis.a2a.stream_capped` | `lane` | E |
| span `a2a.stream_capped` | | E |
| `chassis.idempotency.waiters_full` | | H |
| `chassis.model_route_pinned` | `route` | I |
| `chassis.feedback` | `label`, now from a fixed set | G |

suggested: every name. On `/metrics` they show as `<name>_total` (contract v7, A.3).

## Cost per lane

- **`inprocess`.** One `secrets.token_urlsafe(32)` per run, sent in `ctx` and never checked. Pydantic checks the bounds on each event. The connector counts bytes and events per stream item. One dict copy per native error.
- **`sidecar`.** The same. The loopback proxy has no `RequireRun`, so the secret is sent and ignored. An uncorrelated call costs one route comparison.
- **`remote`.** The same, plus one `hmac.compare_digest` per proxy call and one more header (43 characters) on every model and MCP call from the remote. No new hop and no new credential to mount. Waits on: p50 and p95 on the PoC-5 remote suite on kind (`KIND_DRILLS=1`) and the 6b remote lane on the Mac.

## Contract suites

| Suite | New or changed | Bound to |
| ----- | -------------- | -------- |
| Every port suite | Unchanged. No port changed | |
| `InboundContract` (`chassis_contracts.inbound`, `inbound_cases`) | Every format, native and MCP included, sends `public_message(code)` mid-stream, never the run's own text. The id mask is `masked`, which fits `ID_PATTERN` | `packages/contract-suites/tests/test_inbound_suite.py`, `packages/chassis/tests/test_inbound_contract.py` |
| Interface harness | `PER_RUN_CTX` gains `run_secret`: the probe never echoes it | `chassis_contracts.interface_harness` |
| `poc_harness.remote_lane` | `RemoteLane.run_headers(trace)` sends `traceparent` and `x-chassis-run-secret` | `pocs/poc-05-sandboxed/tests/test_poc05_remote_lane_offline.py`, `test_poc05_hostile_offline.py` |
| Freeze test | Re-pinned (C.4) | `packages/chassis/tests/test_contract_freeze.py` |

Run on 2026-11-06, on the worktree:

```
$ scripts/check_offline.sh packages/chassis/tests/test_contract_freeze.py packages/chassis/tests/test_remote_auth.py \
    packages/chassis/tests/test_feedback_route.py packages/chassis/tests/test_a2a_stream_cap.py \
    packages/chassis/tests/test_idempotency_waiters.py packages/chassis/tests/test_interface_security.py \
    packages/chassis/tests/test_mcp_interface.py packages/chassis/tests/test_chat_materialize.py \
    packages/chassis/tests/test_router_span.py packages/chassis/tests/test_envelope.py packages/chassis/tests/test_events.py -q
223 passed, 1 xfailed in 16.89s
```

The one xfail is R3-06, the idempotency key not scoped by the caller. It is not in this step. Waits on: `make check`, and the kind and Mac drills for the remote lane.

## Open points

- **For the user:**
  - Teach the TypeScript validator the five keywords, then copy the schema (C.4).
  - Move the new caps to `spec.*` (Config reference).
  - A feedback reviewer role (G; ADR-009 question 2).
  - The v7 open point on `/metrics` still stands.
- **Known limits:**
  - Plain mode has no run secret (D.3).
  - The run secret does not isolate runs that share one remote pod (D.2). One run per pod is R6-11.
  - The waiter cap is per replica (H).
  - `tool_call.arguments`, `tool_call.result`, and `end.output` are bounded only by the stream cap (C.2).
  - A lookup written before this change is open to any caller until it expires (G).

## Links

- Decision: [ADR-011](../planning/adr/011-per-run-secret-and-v0-wire-bounds.md). It extends [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md) decision 2 and updates [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md).
- The findings: `docs/reviews/2026-10-29-core-review/proposals.md` and the `findings-R*.md` files beside it (read-only).
- The contract before this one: [contract v7](contract-v7.md). Contract v5, part C (the freeze), holds with the C.3 rule added and the hashes re-pinned (C.3, C.4).
