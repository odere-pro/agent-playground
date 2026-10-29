# Findings R2b: two engines, one contract (LOW overflow)

Session: R2. Same area as [findings-R2-engines.md](findings-R2-engines.md), which holds the Summary and R2-01 to R2-30.
Rubric: [rubric.md](rubric.md).

## Summary

These are the LOW findings that did not fit under the 30-finding cap. None is a risk on its own. Count: 17 LOW, 2 of them proposals.

## Findings

### R2-31 [LOW] [secure] packages/chassis/src/chassis/server/remote_auth.py:108
Problem: `remote_tokens` and the workload's `read_token` (`workload_a2a/auth.py:27-33`) accept any non-blank token, even one character, and failed bearer attempts are not throttled.
Why it matters: the remote listener binds the pod IP, so a short token can be guessed online.
Fix: require a minimum length (suggested: 32 characters) at startup, and name only the variable in the error. Keeps behavior: no (short tokens are refused; tests with short tokens change).
Spec: "given an 8-character token variable, when the chassis or `workload-a2a` starts, then it exits naming the variable and never the value."
Status: proposal

### R2-32 [LOW] [tests] packages/chassis/tests/test_dapr_inbound.py:354
Problem: `proxy_app.py` has no test of the 503 that `_EventsInbound` sends before the lifespan builds the ports, nor of the router rebuild when `ports.events` changes; only the 404 branches are tested (:349, :363).
Why it matters: the module docstring promises the 503, and daprd reads the subscription list once at that moment.
Fix: add `test_proxy_app.py` with those two cases. Keeps behavior: yes.
Spec: "given a proxy app whose public app has not started, when `GET /dapr/subscribe` is called, then the status is 503."
Status: open

### R2-33 [LOW] [maintainable] [shared] packages/chassis/tests/test_poc04_seams.py:1
Problem: two package test files carry a PoC number (`test_poc04_seams.py`, `test_contracts_poc07.py`), and `pyproject.toml:78-79` describes the `stack` and `kind` markers as PoC drills.
Why it matters: the rubric says no PoC number in a package test or marker; a package test should name the behavior.
Fix: rename the files to their behaviors and reword the marker descriptions; leave the env gate names to a separate change. Keeps behavior: yes.
Spec: "given the renames, when `pytest --collect-only packages/chassis` runs, then the same tests are collected and no file name under `packages/*/tests` contains `poc0`."
Status: open

### R2-34 [LOW] [simple] packages/workloads/echo-typescript/src/handle.ts:170
Problem: `handle` is 76 lines and nests `try`, `for`, `for`, `try`; `HandleExecutor.execute` (`a2a_server.ts:122`) is 51 lines.
Why it matters: both break the 50-line rule, and the tool-call step cannot be tested alone.
Fix: extract the header and option setup and the "run one round's tool calls" step (:218-235), and the schema-version gate in `execute` (:127-131). Keeps behavior: yes.
Spec: "given a model that asks for two tools in one round, when the extracted tool step runs, then it yields two `tool_call` events in index order."
Status: open

### R2-35 [LOW] [maintainable] packages/workloads/echo-typescript/src/main.ts:37
Problem: `PORT` is read with `Number(...)` and never checked, and the server has no `'error'` listener (:54-64).
Why it matters: `PORT=abc` or a port in use crashes with an uncaught stack trace and exit 1, while every other start-up fault exits 2 with one line.
Fix: check `PORT` is an integer from 0 to 65535 in the existing `try`, and add `server.on("error", ...)` that prints the message and exits 2. Keeps behavior: yes for valid starts.
Spec: "given `PORT=abc`, when `main` starts, then it exits 2 and stderr names `PORT`."
Status: open

### R2-36 [LOW] [secure] packages/workloads/echo-typescript/src/tools.ts:89
Problem: a transport failure in `callTool` puts the full `exc.message` into `ToolCallError`, while the `isError` path (:93) and the model-error path (`handle.ts:126`) cut it to 200 characters.
Why it matters: the MCP SDK puts the upstream HTTP body into that text, so chassis internals can reach a `tool_error` event unbounded.
Fix: cut the transport message to 200 characters too. Keeps behavior: yes for short messages.
Spec: "given a tool endpoint that answers 502 with a 5000-character body, when the tool is called, then the `tool_error` message is at most 200 characters plus the tool name."
Status: open

### R2-37 [LOW] [maintainable] packages/workloads/echo-typescript/src/a2a_server.ts:34
Problem: `SUPPORTED_SCHEMA_VERSIONS = ["0"]` is hard-coded, `SCHEMA_VERSION` is defined again in `handle.ts:18`, and `Json` in both `handle.ts:27` and `tools.ts:16`; the Python template derives its versions from the vendored schema (`workload_a2a/server.py:100-104`).
Why it matters: a new vendored schema can change its version while the server keeps accepting only "0".
Fix: derive the versions from the schema's `schema_version` consts in `schema.ts`, and import one `SCHEMA_VERSION` and one `Json`. Keeps behavior: yes.
Spec: "given the vendored schema, when the server starts, then `SUPPORTED_SCHEMA_VERSIONS` equals the set of `schema_version` consts in it."
Status: open

### R2-38 [LOW] [simple] packages/workloads/echo-typescript/src/a2a_server.ts:183
Problem: `buildAgentCard` takes an optional `description` that no caller passes, and the `PruningTaskStore.pending` getter (:229) is unused.
Why it matters: both are dead surface to keep in step with the Python template.
Fix: remove the option and the getter. Keeps behavior: yes.
Spec: "given `buildAgentCard` called with name, version, and url, when the card is read, then its description is `chassis workload echo-typescript`."
Status: open

### R2-39 [LOW] [simple] packages/chassis/src/chassis/server/model_proxy.py:641
Problem: the complete-call block in `chat_completions` (:641-660, `settle(None)` on `ModelError` and on `BaseException`) repeats `_complete` in `model_proxy_messages.py:358-382`.
Why it matters: the "settle the hold exactly once" rule that keeps token accounting right lives in two copies.
Fix: after R2-19, give `Hold` an async context manager both routes use. Keeps behavior: yes.
Spec: "given a `ModelError`, a cancel, and a success on each route, when a complete call runs, then the hold settles exactly once."
Status: open

### R2-40 [LOW] [maintainable] pocs/poc-02-two-engines-one-contract/tests/poc02_harness.py:570
Problem: `free_port` repeats `packages/bakeoff/.../procs.py:22` and two chassis test copies, and `sse_frames` (:196) has three siblings (`recording.py:134`, PoC-1 `test_compose.py:209`, `poc04_harness.py:417`).
Why it matters: several helpers for one job is how fixes get missed.
Fix: fold them into the shared test-support module from R2-03. Keeps behavior: yes.
Spec: "given one `free_port` and one `sse_frames`, when the PoC-1, PoC-2, and PoC-4 tests run, then they pass and no local copy remains."
Status: open

### R2-41 [LOW] [tests] pocs/poc-02-two-engines-one-contract/tests/test_tools.py:64
Problem: the criterion is "The tool is defined once, served by the chassis over MCP, and works on all three engines", but three docstrings quote only part of it (:64, :95, :114).
Why it matters: `pocs/CLAUDE.md` asks for the criterion word for word, and a part-quote hides which clause a test proves.
Fix: quote the full sentence, then say in one line which clause the test proves; no name changes, so README evidence stays valid. Keeps behavior: yes.
Spec: "given README line 112, when each `test_tools.py` scenario docstring is read, then it starts with that sentence."
Status: open

### R2-42 [LOW] [tests] pocs/poc-02-two-engines-one-contract/tests/test_boundaries.py:305
Problem: `test_key_name` (:305), `test_key_read_catches` (:175), and `test_key_read_ignores` (:195) name no subject and no outcome.
Why it matters: a failure line like `test_key_name[...]` does not say what broke.
Fix: rename to sentences such as `test_key_name_rule_flags_key_like_names_in_any_case`; grep `pocs/` and `docs/` for citations first (none in the PoC-2 README). Keeps behavior: yes.
Spec: "given the renames, when `pytest --collect-only -q` runs, then the count is unchanged and grep finds no old name in `pocs` or `docs`."
Status: open

### R2-43 [LOW] [tests] packages/chassis/tests/test_a2a.py:213
Problem: `pytest.raises(ValueError)` has no `match`; 24 of 60 `raises` calls in the area have none (8 in `test_server_cli.py`, 4 in `test_server.py`), though some check the value afterward.
Why it matters: a different `ValueError` raised earlier in the path would still pass.
Fix: add `match=` where the test does not check the value. Keeps behavior: yes.
Spec: "given a delta carrying NaN, when `event_to_update` runs, then it raises `ValueError` matching the not-JSON message."
Status: open

### R2-44 [LOW] [tests] packages/chassis/tests/test_model_proxy_messages.py:1090
Problem: a `for _ in range(100): if ...: break; await asyncio.sleep(0.01)` loop waits for a background close; there are 22 real `sleep` calls in the area (for example `test_a2a.py:416`, `test_readiness.py:84`).
Why it matters: a fixed wait can time out on a slow CI machine and fails without saying what it waited for.
Fix: wait on an `asyncio.Event` the fake sets, under `asyncio.timeout`. Keeps behavior: yes.
Spec: "given a streaming client that leaves after the hold window, when the upstream closes, then the event is set and the test asserts one close."
Status: open

### R2-45 [LOW] [docs] pocs/poc-02-two-engines-one-contract/CLAUDE.md:22
Problem: the request path goes from `/v1/run` straight to `SidecarConnector`, skips `RunPipeline`, says to start from `server/app.py` (the route is in `server/interfaces/native.py:282`), and never names `server/model_proxy.py`.
Why it matters: an engineer following the path will not find the route or the pipeline where the doc says.
Fix: write the path as `/v1/run` (`interfaces/native.py`), `RunPipeline`, the connector, and name `model_proxy.py` for the model call. Keeps behavior: yes.
Spec: "given the edit, when `grep -c 'interfaces/native.py\|RunPipeline\|model_proxy.py'` runs on the file, then it prints at least 3."
Status: open

### R2-46 [LOW] [docs] pocs/poc-02-two-engines-one-contract/README.md:13
Problem: 46 of 199 prose sentences are over 20 words; line 13 has 43 and the Question (:9) has 41, and the first three sentences do not say how to run it (that is at :119).
Why it matters: the summary a newcomer reads first is the hardest part to parse.
Fix: split lines 9 and 13, and add a sentence near the top naming `make test-poc POC=02`. Keeps behavior: yes (the Question's meaning is unchanged).
Spec: "given the edit, when the words-per-sentence check runs on lines 1 to 15, then no sentence is over 20 words."
Status: open

### R2-47 [LOW] [docs] packages/workloads/echo-typescript/README.md:16
Problem: the tool-loop section (:16-25) restates `echo-python/README.md:25-32` almost word for word, and `echo-typescript/CLAUDE.md:7` restates it again.
Why it matters: three copies drift; the stale "No tool" in R2-24 shows it already did.
Fix: keep the TypeScript-only points (the SDK pin, `toolStats.listFailures`, the injected `fetch`) and link to echo-python's tool-loop section for the rest. Keeps behavior: yes.
Spec: "given the edit, when the README is read, then it links `../echo-python/README.md#the-tool-loop` and does not restate the loop."
Status: open
