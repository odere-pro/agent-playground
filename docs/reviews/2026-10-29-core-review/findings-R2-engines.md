# Findings R2: two engines, one contract

Session: R2. Area: `packages/chassis/src/chassis/server/` (app, cli, pipeline, correlation, model_proxy, model_proxy_messages, proxy_app, tool_endpoint, remote_auth, security, guard, results, readiness, manifest, marking, evaluator_gate, feedback) and their tests, `packages/chassis/src/chassis/adapters/a2a/`, `packages/workload-a2a/`, `packages/workloads/echo-{python,typescript,langgraph,pydanticai}/`, `docs/contracts/contract-v1.md`, `contract-v2.md`. PoC: `pocs/poc-02-two-engines-one-contract/`.
Rubric: [rubric.md](rubric.md). Overflow (17 more LOW findings, R2-31 to R2-47): [findings-R2b-engines-low.md](findings-R2b-engines-low.md).

## Summary

The area works and is well covered. The credential rules hold: no workload holds a key, the model proxy forwards no `Authorization`, bearer compares are constant time, and the remote token is scrubbed from events. The top risk is the `remote` lane, which treats an untrusted workload as safe. The OpenAI chat route on the proxy and remote listeners has no body cap, and one shared bearer lets a remote charge or write under any in-flight run. The top maintenance risk is `poc02_harness.py`: seven later PoCs import it through `sys.path`, so a PoC test folder is a hidden library. Count: 0 CRITICAL, 6 HIGH, 21 MEDIUM, 3 LOW here; 17 LOW in the overflow file.

Seen outside this area, for its owner: `packages/chassis/src/chassis/profiles.py:323` `_check_cloud` refuses only `fake`, `memory`, and `static`, so `spec.adapters.auth: none` is accepted in the `cloud` profile, and the public `/v1/run` then runs as the anonymous principal with every scope.

## Findings

### R2-01 [HIGH] [secure] packages/chassis/src/chassis/server/model_proxy.py:601
Problem: `POST /v1/chat/completions` on the proxy port and the remote listener parses the whole body with no size cap; `BODY_CAP_BYTES` and `_read_capped` guard only the sibling `/v1/messages` route (`model_proxy_messages.py:280`), and neither proxy app installs a body limit (`remote_auth.py:229-241`).
Why it matters: a remote workload with a valid bearer and an in-flight `traceparent` can send a body of several GB and exhaust the memory of the one process that holds every credential and serves the public port.
Fix: read the chat body through `_read_capped` with the same `BODY_CAP_BYTES`, and answer 413 above it. Keeps behavior: yes (bodies under the cap are unchanged; the cap already exists on the messages route).
Spec: "given a correlated run, when a chat-completions body of `BODY_CAP_BYTES + 1` bytes is posted to the proxy port and to the remote listener, then each answers 413 and the model port is never called."
Status: open

### R2-02 [HIGH] [secure] packages/chassis/src/chassis/server/remote_auth.py:213
Problem: `RequireRun` accepts any `traceparent` that names any in-flight run, and the bearer is one secret shared by all runs; `tool_endpoint.py:152-157` then derives the tool write key from that run.
Why it matters: an untrusted remote serving run A can spend run B's token budget, and can send a tool write under run B's key so B's real write is dropped as a duplicate; `correlation.py:16` already names a per-run credential as the missing piece.
Fix: the chassis mints a per-run secret, sends it with the A2A call, and the remote listener requires it to match the run named by `traceparent`. Keeps behavior: no (a new header on the proxy wire; ask `chassis-architect`).
Spec: "given runs A and B in flight on one remote, when a proxy call carries the valid bearer, B's `traceparent`, and A's run secret, then it gets 403 `run_required` and B's budget and run key are untouched."
Status: proposal

### R2-03 [HIGH] [maintainable] [shared] pocs/poc-03-one-interface-every-client/tests/poc03_harness.py:50
Problem: eight files insert `pocs/poc-02-two-engines-one-contract/tests` into `sys.path` to import `poc02_harness` (PoC-3 `poc03_harness.py:50`, PoC-4 `poc04_conftest.py:27`, PoC-5 `poc05_conftest.py:32`, PoC-6a/6b/6c `tests/conftest.py`, PoC-7 `tests/conftest.py:19` and `demo/demo.py:32`); PoC-6b and 6c insert it but import nothing.
Why it matters: it breaks the rubric's "no `sys.path` insert in `pocs/*/tests`", and a PoC-2 edit can break PoC-3 to PoC-7 with no dependency declared anywhere.
Fix: move the durable helpers (`chassis_app`, `proxy_for`, `on_unix_socket`, `a2a_on_unix_socket`, `serve_tcp`, `uds_transport`, `route_outbound`, `typescript_echo`, `mcp_request`, `sse_frames`) into a test-support module in `packages/` (for example next to `chassis_contracts.recording`), import it by name, and delete the inserts; this touches `pyproject.toml` (mypy and pytest paths). Keeps behavior: yes.
Spec: "given PoC-3 to PoC-7, when `grep -rn sys.path.insert pocs/*/tests pocs/*/demo` runs, then it prints nothing, and `make test-poc POC=03` to `POC=07` collect the same tests and pass."
Status: open

### R2-04 [HIGH] [simple] packages/chassis/src/chassis/adapters/a2a/connector.py:198
Problem: `A2AConnector.run` is 107 lines and nests six levels (`with span`, `try`, `while`, `for`, `try`); it mixes the deadline loop, translation, event validation, the `start.request_id` check, error mapping, and a shielded cleanup.
Why it matters: it is the hot path for every lane, and the cancel and close rules are buried inside the loop, so a change to one risks the others.
Fix: extract `_read_one(responses, deadline)`, `_events_from(raws, request)` (parse plus the mismatch check), and `_finish(...)` (the `finally` body); `run` keeps the span and the error mapping. Keeps behavior: yes.
Spec: "given a stream that stalls past `timeout_ms`, ends with no `end`, sends bad JSON, or sends a wrong `start.request_id`, when `run` is read, then it yields `a2a.timeout`, `a2a.transport`, `a2a.bad_event`, or `a2a.request_mismatch` as today and cancels the task once; `run` is under 50 lines."
Status: open

### R2-05 [HIGH] [simple] packages/workload-a2a/src/workload_a2a/server.py:294
Problem: besides the guarded `mapping.py` copy, the two A2A servers copy more code with no guard: `PruningRequestHandler` and `_terminal_state` (chassis `adapters/a2a/server.py:205-272`, workload `server.py:285-352`) and `_as_dict` and `_error` are identical, and the ordering state machine (chassis `execute` :117-195, workload `_run` :224-283) differs only in the validator.
Why it matters: a fix to the cancel or ordering rules must land twice, and only `mapping.py` has a byte-equal test (`test_workload_a2a_copies.py:17-19`).
Fix: move the identical parts into a second byte-equal module (for example `task_store.py`) and a `drive_stream(stream, validate)` helper, and extend the copies test to it; `workload_a2a` still never imports `chassis`. Keeps behavior: yes.
Spec: "given the bad-event, out-of-order, no-end, exception, and cancel cases, when both servers run them, then they emit the same `workload.*` codes as today, and the copies test fails if the two new copies differ."
Status: open

### R2-06 [HIGH] [docs] docs/contracts/contract-v1.md:229
Problem: v1 says the v0 `Struct` form "is read through v1 and dropped in v2" (also line 154), and `packages/workloads/echo-typescript/CLAUDE.md:12` says to read it "until v2"; contract v5 (`contract-v5.md:560`) keeps that reader until the next schema major, and the code still has it.
Why it matters: v2 to v6 defer to v1 for the A2A mapping, so an engineer can read this as leave to delete a compatibility reader the contract still requires.
Fix: mark both v1 lines "(v5: kept until the next schema major; see contract-v5.md part C, item 6)" and change the TypeScript CLAUDE.md line to say the same. Keeps behavior: yes.
Spec: "given the edits, when `grep -n 'until v2' packages/workloads/echo-typescript/CLAUDE.md` runs, then it prints nothing, and every `dropped in v2` hit in contract-v1.md also contains `v5:`."
Status: open

### R2-07 [MEDIUM] [secure] packages/chassis/src/chassis/server/model_proxy.py:271
Problem: `_error_body` sends `exc.message`, the upstream LiteLLM text, to the workload on the OpenAI proxy route (used at :527 and :655), while the Anthropic route sends fixed text per code (`model_error_to_wire`).
Why it matters: LiteLLM error text can name routes, `api_base` URLs, and provider details, and a remote workload reads all of it; the package rule is fixed text per code.
Fix: send fixed text per `code`, as the messages route does, and keep the upstream text in the log through `note_rejected`. Keeps behavior: yes (status, `code`, and `retryable` unchanged; only `message` text changes).
Spec: "given a model that raises `ModelError('http_500', 'api_base=https://litellm.internal ...')`, when the chat proxy answers a complete call and a stream, then the body carries the fixed text and the upstream text is only in the log."
Status: open

### R2-08 [MEDIUM] [secure] packages/chassis/src/chassis/server/pipeline.py:149
Problem: `_guarded` turns any engine exception into `Error(message=f"{type(exc).__name__}: {exc}")`, and the connector puts `str(exc)` into `a2a.bad_event` (`connector.py:251`, `:257`) and, outside plain mode, into timeout and transport errors (`connector.py:190`).
Why it matters: httpx, SDK, and pydantic text (which quotes the remote's payload as `input_value=...`) reaches the caller's envelope unredacted; only plain mode (`remote.py:180`) uses fixed text today.
Fix: give these `Error` events fixed text per code and send the detail to the log through `redact`. Keeps behavior: yes (`code`, `retryable`, and the event schema unchanged; only `message` text changes).
Spec: "given a sidecar or remote that sends a malformed event, when native `/v1/run` is called, then `error.message` holds none of the remote's text and the log holds it redacted."
Status: open

### R2-09 [MEDIUM] [secure] packages/chassis/src/chassis/adapters/a2a/remote.py:103
Problem: the one token named by `engine.auth.token_env` is both the bearer the chassis sends to the remote (`read_token`) and the bearer the remote listener accepts (`remote_auth.py:106`).
Why it matters: a plain-A2A third-party agent receives the chassis's token on every call and can replay it against the chassis's model proxy and `/mcp`.
Fix: read the listener's token from a separate `engine.auth.proxy_token_env`, falling back to `token_env` when unset. Keeps behavior: yes (the fallback keeps today's configs).
Spec: "given distinct outbound and inbound tokens, when a call to the remote listener presents the outbound token with a valid `traceparent`, then it gets 401 `remote_unauthenticated`."
Status: open

### R2-10 [MEDIUM] [secure] packages/chassis/src/chassis/server/feedback.py:226
Problem: the feedback lookup stores only `trace_id` and `agent`, and `_lookup` (:346-355) checks only the agent, so any caller with the feedback scope can score any `request_id`.
Why it matters: with caller auth on, one tenant can write scores onto another tenant's trace and poison the evaluation data.
Fix: store the caller's subject when the run ends, and answer 404 `unknown_request` when a stored subject differs from the caller's. Keeps behavior: yes with auth off (the subject is `None`); with auth on, only cross-subject calls change.
Spec: "given a run that ended for subject alice, when bob posts `/v1/feedback` for its `request_id`, then the answer is 404 `unknown_request` and nothing is stored."
Status: open

### R2-11 [MEDIUM] [secure] packages/chassis/src/chassis/adapters/a2a/connector.py:253
Problem: a lane stream has no cap on event count or bytes; every event is kept in `Run._seen` (`pipeline.py:366`), and the plain translator caps only the artifact count.
Why it matters: a hostile remote can stream deltas at network speed until `timeout_ms` and grow chassis memory without bound.
Fix: add a per-run output byte cap in the connector that ends the run with a fixed `Error` and cancels the task (the value is `suggested:`). Keeps behavior: yes for normal sizes.
Spec: "given a remote that streams deltas past the cap, when the run reads them, then it ends with one error event, the A2A task is cancelled, and `_seen` stays under the cap."
Status: open

### R2-12 [MEDIUM] [maintainable] packages/workloads/echo-typescript/src/schema.ts:53
Problem: `validateEvent` returns the raw event and never fills schema defaults, while the Python `validate_event` does (`workload_a2a/server.py:112-122`, `:155-157`).
Why it matters: the same event puts different `chassis.event` JSON on the wire (a `metrics` event lacks `cost_usd` and `latency_ms`; a `tool_call` lacks `arguments`), which breaks the "both copies put the same JSON on the wire" rule even though the chassis's `parse_event` fills the same defaults on read.
Fix: merge the per-type defaults under the event after validation, as the Python server does. Keeps behavior: yes at the chassis (the parsed events are equal); only the raw data-part bytes change.
Spec: "given a `handle` that yields `metrics` with only the token counts, route, and attempt, when the executor publishes it, then `chassis.event` holds `cost_usd: null` and `latency_ms: null`, equal to the Python template's output."
Status: open

### R2-13 [MEDIUM] [maintainable] packages/chassis/tests/test_model_proxy_messages.py:1
Problem: the file is 1195 lines, sorted by review history ("correction 1", "review round 2", "the draft's"), holds chat-route tests (:529, :603), and redefines fakes that other files also define (`RecordingModel` :60 and `test_model_proxy.py:321`; `GatedModel` :120 and `test_uncorrelated_cap.py:206`; `_ports` and `_client` in five files; `_frames` here and `test_server.py:60`).
Why it matters: it is 50% over the 800-line limit, a behavior is hard to find by name, and the copied fakes drift.
Fix: move the shared fakes into one proxy test harness with domain names, split the file by behavior (wire, budget, errors, limits, upstream), and move the chat-route tests to `test_model_proxy.py`. Keeps behavior: yes.
Spec: "given the split, when `pytest packages/chassis -k 'messages or model_proxy'` runs, then the same tests are collected and pass, and each file is under 400 lines."
Status: open

### R2-14 [MEDIUM] [tests] packages/chassis/tests/test_model_proxy_messages.py:265
Problem: `test_the_captured_body_streams_a_tool_use_round_trip` is 118 lines with 22 asserts over two HTTP turns, headers, frames, model calls, and counters; `test_the_credentials_appear_nowhere_on_the_messages_route` (:623) is 71 lines.
Why it matters: a failure points at no single behavior, and arrange, act, and assert are mixed.
Fix: split into one test per behavior (turn-1 headers and frames, what reaches the model, the `tool_result` turn, the ignored-params counters) with a shared fixture. Keeps behavior: yes.
Spec: "given the captured CLI body, when turn 1 is posted, then the stream is SSE with `request-id` and the first frame names `big-default`."
Status: open

### R2-15 [MEDIUM] [tests] pocs/poc-02-two-engines-one-contract/tests/poc02_harness.py:1
Problem: the harness is 751 lines and holds app builders, outbound routing, a scripted tool-loop model, socket and TCP servers, a TypeScript launcher, and an MCP client; `test_harness.py` tests only two of them (lines 50 and 88).
Why it matters: about twenty import lines in PoC-3 to PoC-7 depend on it, and `on_unix_socket` and `serve_tcp` have no direct test.
Fix: with R2-03, split it into focused modules (outbound, sockets, TypeScript echo, MCP client), keep `poc02_harness` as a re-export, and test `on_unix_socket` and `serve_tcp` directly. Keeps behavior: yes.
Spec: "given the split, when `uv run pytest --collect-only pocs` runs, then the count is unchanged and every `from poc02_harness import X` still resolves."
Status: open

### R2-16 [MEDIUM] [tests] packages/chassis/tests/test_a2a_plain.py:95
Problem: 439 of 548 tests in this area have no docstring (39 in `test_a2a_plain.py`, 28 in `test_model_proxy_messages.py`, 26 in `test_a2a.py`), and 81 have multi-line docstrings (16 in `pocs/poc-02-*/tests/test_compose_sidecar.py`, 13 in `test_boundaries.py`).
Why it matters: the rubric asks for one plain line per test, and without it a name alone does not say which rule a test pins.
Fix: add one-line docstrings, largest files first, and cut multi-line ones to the criterion or rule in one line; keep the `Exit criterion:` lines as they are. Keeps behavior: yes.
Spec: "given the test tree, when an AST scan counts `test_*` functions whose docstring is missing or longer than one line, then the count is 0."
Status: open

### R2-17 [MEDIUM] [tests] packages/chassis/tests/test_marking.py:132
Problem: 81 of 548 tests wrap asserts in `for` or `if` (for example `test_marking.py:134` and `:141`, `test_security_auth.py:117`, `test_remote_auth.py:120`, `test_a2a_sidecar.py:123`, `test_compose_sidecar.py:120`, and `echo-typescript/test/mapping.test.ts:155` with a `continue`).
Why it matters: the first failing case hides the rest, a `continue` can skip asserts silently, and the failure does not name the case.
Fix: turn the loops into `pytest.mark.parametrize` (or generated `test(...)` cases in TypeScript), and collect stream events into a list before asserting. Keeps behavior: yes.
Spec: "given `/v1/run`, `/v1/chat/completions`, and `/v1/messages`, when a reader token posts to each, then each is its own test case and gets 403."
Status: open

### R2-18 [MEDIUM] [tests] packages/chassis/src/chassis/adapters/a2a/inprocess.py:35
Problem: `load_handle` has four refusal branches (no `:`, missing attribute, not callable, empty parts) and `InProcessConnector.setup` refuses a missing handle and reads the card from config, but no test reaches any of them; only the happy path runs (`test_lane_contract.py:59`).
Why it matters: these are the errors a user sees for a mistyped `spec.engine.handle`, and `inprocess.py` has no test file.
Fix: add `test_inprocess.py` with a parametrized `load_handle` refusal test, a missing-handle setup test, and a card-from-config test. Keeps behavior: yes.
Spec: "given `handle: 'echo'` with no colon, when `load_handle` runs, then it raises `ValueError` matching `module:attribute`."
Status: open

### R2-19 [MEDIUM] [simple] packages/chassis/src/chassis/server/model_proxy.py:401
Problem: the 663-line module holds the uncorrelated cap (:132), `Refusal` (:283), the private `_Hold` (:401), `Admission` and `admit` (:532-582), and the OpenAI wire; `model_proxy_messages.py:58` imports the private `_Hold`, and `model_proxy.py:595` uses a function-local import to break the cycle.
Why it matters: the admission layer both routes share is hidden in one route's module, and the cycle and private import show the seam is in the wrong place.
Fix: move `UncorrelatedCap`, `Refusal`, a public `Hold`, `Admission`, and `admit` into `model_admission.py`; both route modules import it, and the lazy import goes. Keeps behavior: yes.
Spec: "given both proxy routes, when a run is spent, an uncorrelated call passes the cap, or a stream is abandoned, then the 429 bodies and charged tokens are unchanged, and `grep -n _Hold model_proxy_messages.py` prints nothing."
Status: open

### R2-20 [MEDIUM] [simple] packages/chassis/src/chassis/server/app.py:127
Problem: `create_app` is 119 lines; its nested `lifespan` (:138) is 65 lines and repeats `except BaseException: await close_ports(bundle); raise` three times (:147, :158, :165).
Why it matters: the shutdown order in `packages/chassis/CLAUDE.md` is hard to check in one block, and a new background task needs edits in two places.
Fix: register each started resource on an `AsyncExitStack` in shutdown order, and extract `_start_result_events(app)` and `_start_feedback(app, bundle)`. Keeps behavior: yes.
Spec: "given a failure in engine setup, the first config load, or `with_gate`, when the lifespan starts, then the ports close once and the error propagates, and `test_shutdown` still passes."
Status: open

### R2-21 [MEDIUM] [simple] packages/chassis/src/chassis/server/feedback.py:377
Problem: `mount_feedback` is 78 lines, and its nested handler (:401) is 54 lines; it decodes the caller, looks up, checks first-seen, builds, submits, maps four exceptions to `code` in a `try/except/else`, and publishes.
Why it matters: the control flow is hard to follow, and the error mapping cannot be tested without the app.
Fix: extract `_store(ports, config, body, subject)` and `_failure_code(exc)`, so the handler only calls, publishes, and answers. Keeps behavior: yes.
Spec: "given a lookup miss, an id conflict, a state outage, and an unknown `FeedbackError`, when `POST /v1/feedback` runs, then the statuses are 404, 409, 503, and 502 as today, and the handler is under 30 lines."
Status: open

### R2-22 [MEDIUM] [simple] pocs/poc-02-two-engines-one-contract/demo/measure.py:546
Problem: the file is 869 lines and `render` is 298; `bundle` (:207) and `request_for`/`context_for` (:198-205) copy `bundle_for` and `request_and_context` from `poc02_harness.py` (:458, :442).
Why it matters: the PoC-2 measurement numbers come from this file, and it cannot be reviewed or changed in pieces.
Fix: split `render` into one function per report section, move the sampling hooks to their own module, and reuse the harness helpers. Keeps behavior: yes (the rendered report stays byte-equal).
Spec: "given a fixed results fixture, when the new `render` runs, then its output equals the old one, no function is over 50 lines, and the file is under 800."
Status: open

### R2-23 [MEDIUM] [docs] docs/contracts/contract-v1.md:3
Problem: v1 and v2 have no "Superseded" banner (v0 and v4 do), and both state facts that are now false: v1:238 says the TypeScript task store is not pruned, v1:246 and v2:13 say `remote` is not built, v1:305 lists two proxy routes, and v2:29 says there is no caller auth yet.
Why it matters: `packages/chassis/README.md` sends readers to v1 and v2, and nothing warns them.
Fix: add the v0-style banner to both, linking `contract-v6.md` first and saying v1 and v2 hold only where later versions are silent. Keeps behavior: yes.
Spec: "given the edits, when `head -4` of each file is read, then it contains `Superseded` and a link to `contract-v6.md`, and the link check passes."
Status: open

### R2-24 [MEDIUM] [docs] packages/workloads/README.md:8
Problem: the workload docs went stale after PoC-4 to PoC-7: this table says echo-typescript has "No tool" and calls three PoC-6 engines skeletons that yield `not_implemented` (:11-13); line 15 says every workload calls `127.0.0.1:8090` (remote ones use the 8091 listener); `echo-typescript/README.md:77` says CI does not run `npm ci` (`.github/workflows/ci.yml:63` does); `echo-langgraph/README.md:11` says "No OpenTelemetry instrumentation" though `telemetry.py` exists; `workload-a2a/CLAUDE.md:5` omits `telemetry.py`.
Why it matters: the package index and the folder guides give a false picture of what is built and tested.
Fix: correct each line to match the code, naming `telemetry.py`, the tool loop, the 8091 remote listener, and the CI `typescript` job. Keeps behavior: yes.
Spec: "given the edits, when `grep -rn 'No tool\|not_implemented\|does not run\|No OpenTelemetry' packages/workloads packages/workload-a2a` runs, then it prints nothing."
Status: open

### R2-25 [MEDIUM] [docs] packages/chassis/README.md:3
Problem: the README names contract v3 as the contract while `packages/chassis/CLAUDE.md:4` names v6; line 12 says `chassis serve` opens two listeners (the `remote` lane adds a third); the proxy-port line omits `/v1/messages`.
Why it matters: the package's front page sends readers to a contract three versions old and miscounts the listeners.
Fix: link `contract-v6.md` ("v5 and earlier for the rest"), say "two listeners, three in the `remote` lane", and add `/v1/messages`. Keeps behavior: yes.
Spec: "given the edit, when `grep -c contract-v6.md packages/chassis/README.md` runs, then it prints at least 1 and the link check passes."
Status: open

### R2-26 [MEDIUM] [docs] pocs/poc-02-two-engines-one-contract/README.md:3
Problem: the README has no `Builds on:` and no `Hands to:` line; PoC-1, PoC-3, and `docs/templates/poc-readme.md` lack them too.
Why it matters: the rubric requires both, and without them a reader cannot see the chain from PoC-1 to PoC-3 without reading the whole file.
Fix: add `Builds on: PoC-1` and `Hands to: PoC-3` under `Status:`, and add both fields to the template (the template change is for R0 or R8 to place). Keeps behavior: yes.
Spec: "given the edit, when `grep -c '^Builds on:\|^Hands to:' pocs/poc-02-*/README.md` runs, then it prints 2."
Status: open

### R2-27 [MEDIUM] [maintainable] packages/workloads/echo-typescript/src/handle.ts:148
Problem: each parsed SSE chunk is cast to `Json` unchecked, so `data: null`, a non-array `choices`, or a null choice or tool piece (`tools.ts:111`) throws a `TypeError` that the outer `catch` (:237) reports as `connect_error`, retryable.
Why it matters: a malformed model answer is retried as if it were a network fault, and the error code is wrong.
Fix: check the chunk, `choices`, each choice, and each tool piece, and yield `bad_response`, not retryable, otherwise. Keeps behavior: no (the code and `retryable` change for malformed chunks).
Spec: "given a model stream whose payload is `data: null`, when `handle` runs, then it yields `error` with code `bad_response` and `retryable: false`."
Status: proposal

### R2-28 [LOW] [tests] pocs/poc-02-two-engines-one-contract/README.md:116
Problem: the gate line quotes `134 passed, 6 skipped` (140 tests, 2026-10-01), but the folder now collects 148 tests (146 outside `network`); the tests changed on 2026-10-08.
Why it matters: the checkbox rule says evidence is the command and its output, and this output no longer matches the suite.
Fix: re-run `make test-poc POC=02` and update the counts and date. Keeps behavior: yes.
Spec: "given the gate, when `make test-poc POC=02` runs, then the README quotes its pass and skip counts."
Status: open

### R2-29 [LOW] [secure] packages/chassis/src/chassis/server/security.py:163
Problem: `CallerAuth` passes every non-`http` scope straight to the app, while the remote listener refuses them (`remote_auth.py:119`).
Why it matters: a websocket route added to the public app later would skip caller auth and scopes with no warning.
Fix: close websockets with 1008 before accept and pass only `lifespan` through. Keeps behavior: yes (the public app has no websocket routes).
Spec: "given a websocket handshake to the public app, when `CallerAuth` sees it, then it closes with 1008 and the app never runs."
Status: open

### R2-30 [LOW] [simple] packages/chassis/src/chassis/server/cli.py:65
Problem: `_is_loopback` is a line-for-line copy of `_is_loopback_host` in `adapters/a2a/sidecar.py:81`.
Why it matters: a fix to IPv6 or bracket handling would land in one copy only.
Fix: keep one `is_loopback_host` in a small chassis module that the CLI and the sidecar both import (the `workload_a2a` copy stays, since it cannot import `chassis`). Keeps behavior: yes.
Spec: "given `localhost`, `127.0.0.2`, `::1`, `10.0.0.1`, and `not-an-ip`, when the shared helper runs, then it returns True, True, True, False, False."
Status: open
