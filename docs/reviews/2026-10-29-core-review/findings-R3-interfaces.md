# Findings R3: one interface, every client

Session: R3. Area: `packages/chassis/src/chassis/core/inbound.py`, `adapters/openai_compat/`, `adapters/anthropic_compat/` (not `model_wire.py`), `adapters/mcp/agent.py`, the inbound side of `adapters/a2a/` (`server.py`, `mapping.py`), `server/interfaces/`, their chassis tests, `packages/contract-suites/src/chassis_contracts/inbound.py` and `interface.py`, `docs/contracts/contract-v3.md`. PoC: `pocs/poc-03-one-interface-every-client/`.
Rubric: [rubric.md](rubric.md).

## Summary

The four interfaces share one spine (`serve()`), and the OpenAI and Anthropic paths send only `public_message(code)` on errors. Auth covers every run route, and the cassettes hold no key. Every README evidence citation still resolves. The top risk is `BodyLimit`: it trusts `Content-Length`, so a chunked body can pass the size cap. Two other risks follow it. Native and MCP callers get the run's raw exception text. The PoC-3 harness is the `sys.path` hub that PoC-4 to PoC-7 import through. The OpenAI and Anthropic adapters repeat about eight decisions, and those copies have begun to drift.

Checked, fine: OpenAI and Anthropic stream errors (`openai_compat/inbound.py:340-345`, `anthropic_compat/inbound.py:420-428`); `CallerAuth` on `/v1/run`, `/v1/chat/completions` and `/v1/messages` on the public port, and `/v1/mcp` (`server/security.py:67`); the MCP inner hop strips headers (`adapters/mcp/agent.py:65-76`); the A2A inbound app binds to loopback or a Unix socket only; the cassettes in `tests/cassettes/interfaces/` hold no key; `ruff check` is clean on the area.

## Findings

### R3-01 [HIGH] [secure] packages/chassis/src/chassis/server/interfaces/limits.py:121
Problem: `BodyLimit` passes any request that has a `Content-Length` at or under the limit to the app uncounted, so `Content-Length: 1` plus `Transfer-Encoding: chunked` can stream a body of any size.
Why it matters: The lens ran h11 0.16.0 (uvicorn's parser here) and saw it accept both headers and deliver every chunk, so the 1 MiB cap and the 413 are skipped on all four public routes.
Fix: Always count the bytes received in a wrapped `receive`, and treat `Content-Length` only as an early refusal. Keeps behavior: yes.
Spec: "given a POST to /v1/run with Content-Length 1, Transfer-Encoding chunked, and a body over body_bytes_max, when BodyLimit handles it, then the answer is 413 and the app never reads the body."
Status: open

### R3-02 [HIGH] [secure] packages/chassis/src/chassis/adapters/a2a/server.py:186
Problem: `workload.exception` puts `f"{type(exc).__name__}: {exc}"` in the error event, and native `/v1/run` sends that text unchanged in its SSE `error` frame and in `output.error.message`; the MCP tool returns the same envelope.
Why it matters: Rubric "errors do not leak internals": exception text, internal URLs, and upstream bodies reach callers on the public port. Only the OpenAI and Anthropic paths use `public_message(code)`.
Fix: In native `complete` and `stream` (`server/interfaces/native.py:176-189`), send `public_message(code)` and keep the raw text in the log and the span. Keeps behavior: no (the native error `message` text changes; codes and schema do not).
Spec: "given a workload that raises RuntimeError('http://internal:9000 refused'), when a native stream or an MCP call returns the error, then the client sees the fixed public text and the raw text is only in the log."
Status: proposal

### R3-03 [HIGH] [maintainable] pocs/poc-03-one-interface-every-client/tests/poc03_harness.py:49
Problem: The harness inserts the PoC-2 tests folder into `sys.path`. PoC-4 to PoC-7 then import `poc03_harness` pieces (`OutboundRouter`, `chassis_on_unix_sockets`, `ENGINES`, `AGENT`) through their own `sys.path` inserts (for example `poc04_conftest.py:27`, `poc05_conftest.py:32`, `poc-06a .../conftest.py:20`, `poc-07 .../conftest.py:19`).
Why it matters: It breaks two rubric rules: "code that outlives a PoC is in packages/" and "no sys.path insert in pocs/*/tests". Four later PoCs depend on a PoC-3 file name.
Fix: Move the router, the Unix-socket chassis builder, the engine table, and the PoC-2 helpers they use (`a2a_on_unix_socket`, `patch_outbound`, `typescript_echo`) into `chassis_contracts`. Then delete the `sys.path` blocks. Keeps behavior: yes (test-only move).
Spec: "given PoC-4's test_swap_drill.py, when it needs the outbound router, then it imports it from chassis_contracts and no conftest touches sys.path."
Status: open

### R3-04 [HIGH] [tests] packages/chassis/tests/test_anthropic_interface.py:294
Problem: Only one `mcp_servers` check touches the Anthropic raw-JSON merge (`server/interfaces/anthropic.py:105-110`), and it shares a test with `tools`. Three cases are not pinned: that the parsed body wins on a conflict, that a non-dict raw body is tolerated, and that refused keys the SDK type drops are still refused.
Why it matters: The merge is the only way refused parameters get past the SDK TypedDict. A regression would silently accept bodies that the format-gaps note promises to refuse.
Fix: Add parametrized route tests, one per dropped key and one for precedence. Split the `mcp_servers` check out of `test_sdk_refused_parameters_are_bad_request`. Keeps behavior: yes.
Spec: "given an Anthropic body with a key the SDK type drops, when it is posted to /v1/messages on the public port, then it is refused with 400 and the engine never runs."
Status: open

### R3-05 [HIGH] [docs] pocs/poc-03-one-interface-every-client/README.md:3
Problem: The README has no `Builds on:` line and no `Hands to:` line. No PoC README has them yet.
Why it matters: The rubric requires both lines. Today the order lives only in the planning front matter (`depends_on`), so the README does not read as one step in the story.
Fix: Under `Time box`, add "Builds on: PoC-2 (the `handle` contract, the echo engines, the `inprocess` and `sidecar` lanes)". Add "Hands to: PoC-4 (idempotency and client disconnect on every interface, contract v3), PoC-6a (the bake-off calls engines through these interfaces), and PoC-8 (caller identity on the MCP hop)". Write each as a link. Keeps behavior: yes.
Spec: "given the PoC-3 README, when grepped for ^Builds on: and ^Hands to:, then each matches once and every link resolves."
Status: open

### R3-06 [MEDIUM] [secure] packages/chassis/src/chassis/server/idempotency.py:174
Problem: `store_key` is scoped only by agent and the client's key, not by the authenticated principal. Outside R3's area: R4 owns idempotency; it is listed here because it shows on every interface.
Why it matters: Caller B can get caller A's stored response by sending A's key with the same input. With a different input, B gets a 422 that confirms the key is in use.
Fix: Hash the principal's subject into the store key when auth is on. Keeps behavior: no (replay across principals stops; entries stored before the change are not found).
Spec: "given caller A finished a run with Idempotency-Key K, when caller B sends K with the same input, then B never gets A's stored response."
Status: proposal

### R3-07 [MEDIUM] [simple] packages/chassis/src/chassis/server/interfaces/serve.py:158
Problem: `serve()` is 83 lines and holds four jobs: the idempotency outcome (184-198), the open run and trace-id handling (199-217), the answer, and the failure fallback (231-240).
Why it matters: Rubric "functions are under 50 lines". It is the spine every interface goes through, so it must stay easy to review.
Fix: Extract `_begin_idempotency(...)` and `_open_or_refuse(...)`, so that `serve` keeps only the sequence. Keeps behavior: yes.
Spec: "given a keyed call whose trace id is in use, when serve runs, then the claim is released and 409 trace_id_in_use comes back, as now."
Status: open

### R3-08 [MEDIUM] [simple] packages/chassis/src/chassis/adapters/anthropic_compat/inbound.py:380
Problem: `stream` is 72 lines. Its nested `frames` closure nests 5 levels deep and mutates `opened` through `nonlocal`. The OpenAI `stream` (`openai_compat/inbound.py:349`, 44 lines) is flat.
Why it matters: Rubric "functions are under 50 lines" and "nesting is at most 4 levels".
Fix: Move `_start_frame`, `_text_frames(opened, text)`, and `_closing_frames(usage)` to module level, so `frames()` becomes a short loop. Keeps behavior: yes.
Spec: "given a run with two deltas then end, when streamed, then the frames are message_start, content_block_start, 2 content_block_delta, content_block_stop, message_delta, message_stop."
Status: open

### R3-09 [MEDIUM] [simple] packages/chassis/src/chassis/server/interfaces/serve.py:402
Problem: `_fenced` nests 5 levels (`async with`, `while`, `try`, `except`, `if`) and defines a `nonlocal` closure inside the loop.
Why it matters: Rubric "nesting is at most 4 levels". This is the cancel and `uncancel` logic for a lost claim, which is hard to review at this depth.
Fix: Extract `_next_or_lost(events, claim) -> Event | None`, so that `_fenced` becomes a three-line loop. Keeps behavior: yes.
Spec: "given a claim lost while the engine waits, when _fenced runs, then the task is uncancelled and one retryable idempotency_in_progress error ends the stream."
Status: open

### R3-10 [MEDIUM] [simple] packages/chassis/src/chassis/adapters/openai_compat/inbound.py:327
Problem: Both chat adapters repeat the same decisions in their own code:
- "the run ended without an end event" and the `engine_error` fallback (OpenAI 327 and 377 as inline literals; Anthropic 208-209, 371-373, 431).
- the `x-should-retry` header (OpenAI 400, Anthropic 456).
- the token usage from `metrics` (OpenAI 420, Anthropic 184).
- the id headers (OpenAI 416, Anthropic 168).
Why it matters: Rubric "no second helper that does what one already does". Four copies of one decision will drift.
Fix: Add `NO_END_MESSAGE`, `run_failure(response)`, `retry_header(retryable)`, `token_counts(metrics)`, and one id-headers helper to `core/inbound.py`, then call them from both adapters. Keeps behavior: yes.
Spec: "given a Response with status error and no error event, when either chat adapter completes it, then the code is engine_error and the message is 'the run ended without an end event'."
Status: open

### R3-11 [MEDIUM] [simple] packages/chassis/src/chassis/adapters/openai_compat/inbound.py:205
Problem: The `messages_max` check is written twice with the same text (OpenAI 205-208, Anthropic 265-268) and repeats `limits.messages_over` (`server/interfaces/limits.py:55`). The plumbing that sets it per request is copied too: `_add_route` with `live_limits` (`server/interfaces/openai.py:78`, `anthropic.py:85`) and `_messages_max` (`openai.py:110`, inlined at `anthropic.py:128-131`).
Why it matters: Rubric "no second helper". A change to how a reload changes the cap has to be made in two places.
Fix: Add one `over_cap(count, cap, what)` helper, which `messages_over` also calls, and one `live_messages_max(...)` in `interfaces/limits.py`. Keeps behavior: yes. Dropping the mutable adapter field is a separate proposal, because `enforce_limits` counts turns differently.
Spec: "given messages_max 4 and 5 chat messages, when sent to either chat route, then 400 limit_exceeded says 'messages: 5 is more than the limit of 4'."
Status: open

### R3-12 [MEDIUM] [maintainable] packages/chassis/src/chassis/adapters/anthropic_compat/inbound.py:61
Problem: The Anthropic adapter imports `UnsupportedMessage`, `is_empty`, and `text_of` from `adapters/openai_compat/messages.py`, and `model_wire.py:24` imports `is_empty` from there too.
Why it matters: One wire format depends on a sibling format for helpers that are not OpenAI-specific. A change for OpenAI can then break Anthropic.
Fix: Move the format-neutral helpers into a neutral module, for example `adapters/chat_common.py`. Keeps behavior: yes.
Spec: "given the Anthropic adapter, when its imports are listed, then none names openai_compat."
Status: open

### R3-13 [MEDIUM] [simple] packages/chassis/src/chassis/server/interfaces/anthropic.py:105
Problem: The route takes a parsed `MessageCreateParams`, re-reads `await http.json()`, and hand-merges every raw key under the parsed body.
Why it matters: The route needs two views of one body, and unread nested keys (for example `metadata`) are walked by `materialize` with no depth cap. The lens found no validation bypass, since parsed keys win and `BodyLimit` already ran.
Fix: Merge only the raw keys the adapter reads (`mcp_servers`, `container`, `output_config`, `temperature`, `top_p`, `top_k`), and do not pass `raw` alone. Keeps behavior: yes.
Spec: "given a body with a deeply nested unknown key, when posted to the Anthropic interface (public port), then the key is dropped and no recursion error occurs."
Status: open

### R3-14 [MEDIUM] [simple] packages/chassis/src/chassis/adapters/openai_compat/inbound.py:154
Problem: Two implementations read lazy iterators and turn a `ValidationError` into text. One is `_plain` plus `_validation_text` (OpenAI 154-175: `messages.0.content`, at most 3 lines). The other is `materialize` plus `InvalidBody` (Anthropic 121-152: `messages[0].content`, no cap).
Why it matters: Rubric "no second helper". The 400 text already differs between the two formats.
Fix: Write one `materialize(value, path)` in the shared module, with one location format. Keeps behavior: no (one format's `invalid_body` text changes).
Spec: "given a lazy messages iterator that fails at item 1, when either chat route reads it, then the 400 invalid_body text names the item the same way."
Status: proposal

### R3-15 [MEDIUM] [simple] packages/chassis/src/chassis/adapters/anthropic_compat/inbound.py:247
Problem: `to_request` is 54 lines. Both adapters walk the turns the same way and build `input.data` (`system`, `history`) in their own code (Anthropic 285-289, OpenAI `_task_input` 288-290).
Why it matters: Rubric "functions are under 50 lines" and "no second helper". The `input.data` shape is part of the wire contract, and it is built in two places.
Fix: Add a shared `task_input(text, system_parts, history)`, and split `_check_max_tokens` and `_check_stream` out of Anthropic's `to_request`. Keeps behavior: yes.
Spec: "given system text and one earlier turn, when mapped by either adapter, then input.data is {system, history: [{role, text}]} with the same values."
Status: open

### R3-16 [MEDIUM] [simple] pocs/poc-03-one-interface-every-client/tests/poc03_support.py:114
Problem: `serve_on_uds` is a second Unix-socket uvicorn helper beside `chassis_contracts.interface.serve_on_unix_sockets` (`interface.py:207`). `sse_data` (192) is one of five SSE parsers: `poc02_harness.sse_frames`, `poc04_harness.sse_events`, `_data` in `test_openai_interface.py:85` and `test_anthropic_interface.py:118`, and `test_inbound.py:189-205`.
Why it matters: Rubric "no second helper that does what one in packages/ already does", and "helpers live in a harness".
Fix: Keep one SSE parser and one socket server in `chassis_contracts`, and make every caller use them. `serve_on_uds` runs on the test's own loop, so check loop-bound SDK clients when you switch. Keeps behavior: yes.
Spec: "given test_exit_criteria, when it serves lane_app over a socket and reads SSE, then it uses the package helpers and the same scenarios pass."
Status: open

### R3-17 [MEDIUM] [tests] pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py:150
Problem: The file is 612 lines, and about 200 of them are helpers. It holds 7 tests (16 ids) for 6 criteria. `test_exit_1` makes about 20 asserts on the CI yml, `Makefile`, and `pyproject.toml`, and `test_exit_5` (389) checks four behaviors.
Why it matters: It does not read as a spec, and a helper change for one criterion shows up in the diff of another.
Fix: Keep the file and every node id, so that README lines 13-34 and format-gaps lines 35, 52, 53, and 87 stay valid. Move the helpers into a `poc03_exit_checks.py` harness, and split each multi-behavior test. Keeps behavior: yes.
Spec: "given the PoC-3 exit criteria, when a reader opens test_exit_criteria.py, then each criterion shows one scenario test whose docstring names the criterion word for word."
Status: open

### R3-18 [MEDIUM] [tests] packages/contract-suites/src/chassis_contracts/inbound.py:797
Problem: Loops and branches wrap asserts:
- seven loops in the inbound suite (567, 605, 624, 703, 742, 773, 797).
- `if` branches and an early `return` at 820-825, 841, and 853.
- the same pattern in `interface.py` (654-661, 704-708, 830-851).
- about 12 loops in the chassis tests (for example `test_interfaces.py:546`, `test_mcp_interface.py:288`).
Why it matters: Rubric rules "parametrize instead of loops" and "no if or for around asserts". The first failing case hides the rest, and a wrong flag silently stops an assert.
Fix: Parametrize on `stream`, `mode`, and the case lists, using the suite's existing `pytest_generate_tests` hook (`interface.py:650`). Split each branch into two tests keyed on a class flag. Keeps behavior: yes.
Spec: "given each refused body case, when to_request is called, then it raises Refused with the declared status and code, and each case is its own test id."
Status: open

### R3-19 [MEDIUM] [tests] packages/chassis/tests/test_openai_interface.py:218
Problem: `test_refused_validation_and_not_ready_are_openai_errors` makes six requests that cover four behaviors. The same pattern is in `test_anthropic_interface.py:294`, `:319`, and `:347`, in `test_interface_wire.py:97`, and in `InterfaceContract.test_manifest_matches_config_and_openapi` (`interface.py:897`, about 25 asserts).
Why it matters: Rubric "one behavior per test". When one of these fails, the result does not say which behavior broke.
Fix: Split each test along the behaviors its name lists, and parametrize the invalid-body cases and the lanes. Keeps behavior: yes.
Spec: "given an OpenAI interface (public port) body with a bad role, a bad part, no messages, or invalid JSON, when posted, then the answer is 400 invalid_body with x-should-retry false."
Status: open

### R3-20 [MEDIUM] [tests] packages/chassis/tests/test_openai_inbound.py:52
Problem: 130 of the 152 tests in the nine chassis interface test files have no docstring. For example, 25 of 26 in `test_openai_inbound.py` and 22 of 23 in `test_interfaces.py` have none.
Why it matters: Rubric "the docstring is one line in plain words". Without a docstring, the name is the only spec text.
Fix: Add a one-line plain-word docstring to each test. Keeps behavior: yes.
Spec: "given any test in these files, when a reader opens it, then a one-line docstring states the behavior under test."
Status: open

### R3-21 [MEDIUM] [tests] packages/chassis/tests/test_mcp_interface.py:478
Problem: `test_the_inner_hop_forwards_only_the_correlation_headers` sends no `Authorization` and no `x-api-key`, so no MCP test checks the forward-and-strip rule in `adapters/mcp/agent.py:70-72` as a whole.
Why it matters: The auth forwarding and the strip together make up the security of this hop. `test_security_auth.py:226` checks the forward but not what is stripped.
Fix: Add an MCP test where the caller sends `Authorization` and `x-api-key`, and assert that only `authorization` reaches `/v1/run`. Parametrize the absent-header asserts at 501. Keeps behavior: yes.
Spec: "given an MCP caller with a bearer token and an x-api-key, when the tool runs, then the inner /v1/run call carries the same Authorization and no x-api-key."
Status: open

### R3-22 [MEDIUM] [tests] pocs/poc-03-one-interface-every-client/tests/test_openapi_props.py:267
Problem: Three tests are named `test_finding_*` (267, 280, 312) and have multi-line docstrings that narrate history. The assert at 275, `in (400, 422)`, is weaker than its docstring, which says "is 422".
Why it matters: The names describe the review that found each case, not the behavior. A 400 also passes where the docstring promises a 422.
Fix: Rename the tests to the behavior, for example `test_run_refuses_a_value_of_the_wrong_json_type_with_422`. Assert 422 once you confirm the endpoint always returns it. Update README:21 and format-gaps lines 14 and 67 in the same change. Keeps behavior: yes (only the test changes).
Spec: "given a /v1/run body with budget.max_tokens true, when posted, then the answer is 422 and the value is not coerced."
Status: open

### R3-23 [MEDIUM] [docs] pocs/poc-03-one-interface-every-client/README.md:1
Problem: The README opens with a title, `Status`, `Planning doc`, and `Time box`, and "How to run" starts at line 41. The evidence bullets are single long lines (:17 is 133 words, :52 is 132, :19 is 114), and 14 of about 76 sentences are over 20 words.
Why it matters: The rubric says the first three sentences give what, why, and how to run, and that a sentence stays under about 20 words. A reviewer cannot scan for one piece of evidence.
Fix: Add three sentences under the title: what this is, why it exists, and `make test-poc POC=03`. Split each evidence bullet into `Code:`, `Tests:`, `Run:`, and `Caveat:` sub-bullets, keeping every cited test id. Keeps behavior: yes.
Spec: "given a new reader, when they read the first three sentences of the PoC-3 README, then they know what it is, why it exists, and the command to run it."
Status: open

### R3-24 [MEDIUM] [docs] pocs/poc-03-one-interface-every-client/notes/2026-10-01-debt.md:49
Problem: The debt note says `make check` takes "about 100-130 s", and `backlog-changes.md:25` says the same. The README run it points to (README:39) took 181 s. Items that later PoCs closed still read as open: client disconnect (:7, closed in contract v3) and "no caller auth yet" (:21, and `format-gaps.md` :12, :43, :49; closed by `CallerAuth`, contract v6).
Why it matters: The note's own "split the CI job past 3 minutes" trigger has been met, and the text says it has not. README:58 sends readers to this note, so they would plan work that is already done.
Fix: Quote the gate time once (README:39) and link to it from the notes. Add a dated status line at the top of the debt note that lists what is closed and where. Keeps behavior: yes.
Spec: "given a debt item closed by a later contract, when a reader opens the debt note, then the top says it is closed and links the contract section."
Status: open

### R3-25 [MEDIUM] [docs] pocs/poc-03-one-interface-every-client/notes/2026-10-01-open.md:226
Problem: Stale names with no correction marker:
- :226 says `InboundAdapterContract` is bound in `test_contracts.py`; it is bound in `test_inbound_contract.py`.
- :193 shows the LiteLLM entry `echo_agent`; the config has `chassis_agent` (`deploy/compose/litellm/config.yaml:26`).
- :251 cites `tests/test_interfaces.py`, now `test_interface_contract.py`, and the correction comes only at :277. The code docstring `tests/poc03_support.py:4` has the same stale name.
Why it matters: A reader who follows these lands on the wrong file or key, and nothing says the text is stale.
Fix: Add a "Correction (at close)" marker at :193 and :226, as :43 and :186 already do, and add a pointer to the :277 correction at :251. Fix the docstring at `poc03_support.py:4`. Keeps behavior: yes.
Spec: "given each file, test, or config key cited in the open note, when looked up, then it exists or the same section says it was corrected."
Status: open

### R3-26 [MEDIUM] [docs] pocs/poc-03-one-interface-every-client/notes/2026-10-01-format-gaps.md:68
Problem: Some content is written in several places:
- The status, retry, and error-extras rows (:68-70) and the MCP rows (:80-86) restate contract v2, and `open.md:105-115` holds a third status table that has already drifted (it lacks `limit_exceeded` and 413).
- The `allow_all_keys` gateway caveat is written five times: README:17, debt:53, backlog-changes:19 and :36, and demo:24.
Why it matters: Rubric "a doc says a thing once". These copies have already drifted.
Fix: Keep contract v2's status table and the caveat in debt:53, and replace the other copies with a link plus the test column. Mark the table in open.md §3 as superseded. Keeps behavior: yes.
Spec: "given the status table or the allow_all_keys caveat, when grepped across the PoC-3 docs, then the full text appears once and the rest link to it."
Status: open

### R3-27 [MEDIUM] [docs] pocs/poc-03-one-interface-every-client/README.md:14
Problem: The chat routes are named without their port in five places: README:14 `POST /v1/messages`, backlog-changes:11 and :35, and open.md:300 and :307 `/v1/chat/completions`.
Why it matters: The proxy port serves the same paths. `packages/chassis/CLAUDE.md` requires "the OpenAI interface (public port)" or "the model proxy (proxy port)", and the same care for the two `/v1/messages` routes.
Fix: Name the port in the same sentence at each place. Keeps behavior: yes.
Spec: "given any mention of /v1/chat/completions or /v1/messages in PoC-3 docs, when read, then the same sentence names the public or the proxy port."
Status: open

### R3-28 [MEDIUM] [docs] pocs/poc-03-one-interface-every-client/README.md:17
Problem: The scope item says "FastMCP 4 (`from_fastapi`, curated route maps)", but its own evidence and contract v2 say the tool is built with `FastMCP.from_openapi`. Line :60 also labels contract v2 "the public interface contract" and does not mention v3 to v6, which add idempotency, the 499, and caller auth to the same routes.
Why it matters: A ticked box that names the wrong API teaches the wrong design, and a reader takes v2 as current.
Fix: Change :17 to "`FastMCP.from_openapi` over the `/v1/run` operation, with route maps". Relabel :60 "contract v2: the public interface contract as PoC-3 left it" and add a link to the later versions. Keeps behavior: yes.
Spec: "given a ticked scope item, when compared with its evidence, then both name the same API."
Status: open

### R3-29 [LOW] [secure] packages/chassis/src/chassis/server/interfaces/errors.py:82
Problem: Native `/v1/run` and `/v1/mcp` keep FastAPI's default 422 body, which includes pydantic `input` and `ctx`. That echoes up to 1 MiB of the caller's input.
Why it matters: It breaks the "input is never echoed" rule that the OpenAI and Anthropic interfaces follow (`validation_message`), and the declared `DetailIssues` schema has only `loc`, `msg`, and `type`.
Fix: Give the native routes a handler that returns only `loc`, `msg`, and `type`. Keeps behavior: yes (the body then matches its declared schema).
Spec: "given a native body with budget.max_tokens -1, when the 422 is returned, then each detail item has only loc, msg, and type."
Status: open

### R3-30 [LOW] [secure] packages/chassis/src/chassis/server/interfaces/native.py:298
Problem: A direct caller can send `x-chassis-interface: mcp` to `/v1/run`. That turns off the disconnect watch, labels telemetry `mcp`, and re-mints a trace id that is in use instead of returning 409. The comment at :300 says that "only loses streaming". The header also has two names: `INTERFACE_KEY` here and `INTERFACE_HEADER` (`adapters/mcp/agent.py:45`) on the writer side.
Why it matters: A run whose client has left keeps going until its timeout, and the comment understates the trust this header carries.
Fix: Mark the MCP hop with an ASGI scope flag set by the in-process transport, and read it through one constant. Fix the comment. Keeps behavior: yes for real MCP calls.
Spec: "given a direct POST to /v1/run with x-chassis-interface: mcp, when it is handled, then it is labeled native, its disconnect is watched, and a trace_id in use gets 409."
Status: open
