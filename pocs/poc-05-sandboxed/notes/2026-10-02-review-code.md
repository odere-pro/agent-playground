# PoC-5 code review, waves 0-2 (read-only, Python code and tests)

Reviewer: `reviewer` agent, 2026-10-02. Scope: the files listed in the orchestrator's file list, minus `deploy/`. Nothing was edited except this note.

## Verdict: retry

Two high findings block the PoC-5 exit criteria. First, the tool endpoint never shows a tool list refreshed after startup. Second, code run by `run_python` can outlive its call inside the shared sandbox pod (reproduced). Three medium findings are correctness gaps in the write-key derivation, the uncorrelated cap, and token rotation.

Counts: high 2, medium 4, low 6.

## Findings, most severe first

### H1. `/mcp` serves the tool list from startup only, so the background refresh never reaches workloads

- Where: `packages/chassis/src/chassis/server/tool_endpoint.py:124-128` and `packages/chassis/src/chassis/adapters/mcp/server.py:185-186`.
- Cause: `build_mcp_server` calls `tools.list_tools()` once, inside the lifespan, and adds one `PortTool` per definition. `McpGatewayTools.start()` refreshes `_tools` every 60 s, but nothing rebuilds or re-reads the FastMCP tool set.
- Failing scenario: on kind, LiteLLM is not ready when the chassis starts, for example because the connection is refused or the NetworkPolicy is not applied yet. The first `refresh()` returns False, the endpoint opens with an empty list, and `chassis.tools.refresh_failed` is counted. The next refresh, 60 s later, fills `McpGatewayTools._tools`, but a workload's `tools/list` on `/mcp` still returns `[]` for the life of the pod. The same happens when a tool is added to the key's allow-list later. A removed tool stays listed; the gateway still refuses the call, so this is a stale list, not a bypass.
- Why the tests miss it: `test_tool_endpoint_gateway_lifecycle.py:128` (`test_a_failed_first_refresh_does_not_stop_startup_and_is_counted`) asserts only the counter and the call order. Nothing lists tools over `/mcp` after a later refresh succeeds.
- Plan disagreement: plan section 2.7 says the list is refreshed "every TOOLS_REFRESH_S in the background". That holds for the cache, not for what a workload sees.
- Fix, either option:
  - (a) Make the FastMCP server read the port on each request. Add a FastMCP middleware whose `on_list_tools` returns `port.list_tools()` mapped to `PortTool`, with `on_call_tool` resolving by name. Or rebuild the tool set after each successful `refresh()` (the endpoint already holds `on_refresh_failed`; add `on_refreshed`).
  - (b) Treat an empty first list as a startup dependency. Raise a `ConnectionError` so `DeferredStartup` retries and `/ready` stays `starting`.
- Add a test: the first refresh fails, the second succeeds, and `tools/list` over `/mcp` shows `glossary_lookup`.

### H2. `run_python`: a grandchild that calls `setsid()` outlives the call (reproduced)

- Where: `packages/code-runner/src/code_runner/runner.py:117-119` (`_kill_group` kills only the child's process group) and `:214-217`. The docstring at `:161` says "no grandchild outlives the call".
- Reproduction (offline, macOS). Code passed to `run_python(..., timeout_s=3)`: fork, the child calls `os.setsid()` and forks again, and the grandchild sleeps 1.5 s, then writes a file. Output:
  ```
  result 0 False 'parent done\n' truncated False
  marker written after call returned: True
  ```
  Script: the session scratchpad `grandchild.py`. The call returned, `truncated` was False, and the grandchild kept running and wrote after the return.
- Consequence: the sandbox pod is shared by every call, and every child runs as the same uid. A daemon left by one call can read the next calls' `/tmp/run-*/main.py` and their output files, which is another tenant's code. It can also rewrite `main.py` in the window between `_make_workdir` and the bootstrap's `open("main.py")`, so another run executes its code. The pod boundary (gVisor, no egress) holds against the cluster, but not between calls. Plan section 2.8's "fresh directory, no files kept" is not true while a daemon lives. `RLIMIT_CPU` does not stop a daemon that mostly sleeps, and the PID limit only caps the count.
- Fix, either option:
  - Run each child under a uid of its own (the server keeps 10003; children run as, say, 10004). After each call, kill every process of that uid: a forked helper that `setuid(10004)` and `kill(-1, SIGKILL)`. This is safe because `MAX_CONCURRENT` is 1.
  - Or put each call in a PID namespace of its own (`unshare --pid --fork`, which gVisor supports), so the namespace dies with its init.
  - Make the workdir unreadable to other uids either way.
- Add a test with the setsid daemon: after the call returns, no process from the call is alive. Record the limit in the threat model until then.

### M1. The write-key derivation is not scoped to the agent and ignores whether idempotency is on

- Where: `packages/chassis/src/chassis/server/tool_endpoint.py:47-53` and `packages/chassis/src/chassis/server/correlation.py:137-139`.
- Cause: `run_key = key_hash(client key)` is a bare sha256 of the client's string. The tool key is `tk1:` + sha256(`run_key|tool|args|nonce`): no agent name and no chassis or tenant scope. The tool servers (`fake-mcp-server`, `code-runner`) dedup by that key alone.
- Failing scenarios:
  - (a) Two agents, A and B, share the gateway and `note_write`. One client, or two clients that pick the same key, sends `Idempotency-Key: order-42` to both with input that makes the same `note_write({"text": "x"})` call. B's write is deduped, and B gets A's result (A's note id). With `run_python`, B gets A's cached output without running.
  - (b) `spec.idempotency.enabled: false`, or the PoC-4 entry has expired after `ttl_s`. A client reuses a key for a new request that makes the same tool call, and the write is silently skipped. PoC-4 checks the fingerprint only while its entry lives, but the tool server's dedup has no TTL tied to it.
- Also: the docstring says "the request_id when the request had no key", but `pipeline.py:179` always mints a key, so `idempotency_key_hash` is never None in the real path. `test_tool_idempotency.py:224` (`test_no_client_key_falls_back_to_the_request_id`) builds a record with `idempotency_key=""`, a state the pipeline never produces, so it passes for a path that does not run.
- Fix:
  - Derive `run_key = sha256(f"{agent}|{key_hash}")`. Use it only when the key was sent by the client and idempotency is enabled. Otherwise use `request_id`; carry `key_from` into `RunRecord` or set the hash only when `key_from != "minted"`.
  - Add a test with two agents and the same client key: two effects.
  - Update the plan's section 2.6 formula.

### M2. The uncorrelated cap is not a bound: concurrency, unbounded per-call `max_tokens`, and early-closed streams

- Where: `packages/chassis/src/chassis/server/model_proxy.py:283-285`, `:413`, `:333-337`.
- Failing scenarios:
  - (a) Concurrency. With cap 20000, a workload fires 50 uncorrelated calls at once. All of them see `spent == 0` and pass, and each is charged only on settle. This repeats every window, not "once" as the docstring at `:42-43` says.
  - (b) Per-call size. `_Hold.max_tokens = wanted` for an uncorrelated call: `max_tokens: null` goes upstream with no limit (the route's default). One call can spend many times the cap.
  - (c) Early close. A stream the client closes before the last frame is never charged. `_HeldStream.__call__`'s `finally` calls `release()`, which sets `_done` and charges nothing for an uncorrelated call. Usage arrives only in the final chunk. If Starlette cancels the response while it is blocked in `send`, `_sse`'s `finally` runs after `release()` and is then a no-op.
- Tests: `test_uncorrelated_cap.py` has no concurrent case and no early-close case.
- Fix:
  - Reserve at start as the correlated path does. Clamp `max_tokens` to `limit - spent - reserved` (refuse at 0, and use that remainder when unset), hold it in the cap, settle the actual usage, and on `release()` of a stream that started, charge the reservation.
  - Add tests: `asyncio.gather` of N calls, and a stream closed after the first delta.

### M3. Token rotation is not downtime-free in the A2A direction (code and plan disagree)

- Where: `packages/chassis/src/chassis/adapters/a2a/remote.py:135` (sends `token_env` only) and `packages/workload-a2a/src/workload_a2a/auth.py:38-56` (accepts one token).
- Plan: section 2.2, plan line 86, says "Rotation without downtime ... the chassis's remote listener accepts either; the connector sends the current one."
- Failing scenario: in the runbook's order (rotate, restart the remote, swap), the restarted remote accepts only the new token, while the chassis still sends the old one. The card fetch and every message get 401 until the chassis restarts. Swapping the chassis first fails the same way the other way round. Only the remote-to-chassis direction has the previous token.
- Fix: add `--previous-token-env` to `workload-a2a serve` and accept either token in `BearerTokenMiddleware`, compared against both as `BearerAuth` does. Or record the outage window in the plan and ADR-005.

### M4. Gateway error mapping: an HTTP status from any request in the session overrides the JSON-RPC error

- Where: `packages/chassis/src/chassis/adapters/mcp/gateway.py:163-170`.
- Cause: `_Statuses.hook` records every 4xx and 5xx response in the session, including the session-close `DELETE`, which runs inside `async with` before the `except`. `_failure` checks statuses before `MCPError`.
- Failing scenario: a gateway or server in stateless mode answers the close `DELETE` with 405 or 404. Then an unknown tool (-32601) or invalid params (-32602) error becomes non-retryable `tool_unavailable`, not `unknown_tool` or `bad_arguments`.
- Status: not verified against LiteLLM. The in-process fake runs stateful, so the binding passes.
- Fix: check the `MCPError` leaves first and fall back to statuses only when there is none. Or record statuses only for `POST` requests. Cover it in the T-spike against LiteLLM.

### L1. Tools start twice when the remote listener is on

- Where: `packages/chassis/src/chassis/server/remote_auth.py:191`, plus `proxy_app`'s own `mount_tool_endpoint`.
- Each mount wraps the public lifespan and runs `_start_tools`, so there are two sequential first refreshes. With a black-holed gateway (a NetworkPolicy drop, so a timeout rather than a refusal), startup waits 2 x 30 s. Two FastMCP apps are also built.
- Fix: start and stop the port once, keyed on the port object.

### L2. The refresh loop dies silently if `on_refresh_failed` raises

- Where: `packages/chassis/src/chassis/adapters/mcp/gateway.py:257` and `:268-271`.
- The callback runs inside the `except` block of `refresh()`. An exception from telemetry propagates, the background task ends, and no further refresh happens.
- Fix: guard the callback, and log `_refresh_loop` failures and continue.

### L3. Fakes and tool servers disagree on a reused key with other arguments

- `InMemoryTools` returns the first result; `test_tool_idempotency.py:129` pins this.
- `fake-mcp-server` also returns the first result.
- `code-runner` refuses with `bad_arguments` (`server.py:89-91`).
- The contract suite does not pin either behavior. The derived key includes the arguments, so this matters only on a collision (see M1), but the fakes should behave like the real thing.
- Fix: pick one behavior (refuse is safer) and add a contract case.

### L4. Values the epic does not give, not marked `suggested:`

- `packages/code-runner/src/code_runner/server.py:27-28`: `DEFAULT_TIMEOUT_S = 5`, `MIN_TIMEOUT_S = 1`.
- `packages/code-runner/src/code_runner/runner.py:48-49`: `_DRAIN_S`, `_READ_CHUNK`.
- `packages/chassis/src/chassis/server/remote_auth.py:170`: the counter name `chassis.remote.run_required` is unmarked, while `AUTH_FAILED` is marked.
- `packages/chassis/src/chassis/server/model_proxy.py:83`: `WINDOW_S` is fine, because it comes from the field's "per minute".

### L5. A cancelled first caller fails every waiter on the same key

- Where: `packages/code-runner/src/code_runner/server.py:74-78`.
- A client disconnect cancels the first caller, and every waiter sharing the key gets `run_failed`. The retry then runs again. This is acceptable, but it is not documented in the README's idempotency section.

### L6. The gateway contract's no-key write case never reaches the server

- Where: `packages/chassis/tests/test_tool_gateway_contract.py`, binding `test_write_without_key_is_refused_with_no_effect`.
- The adapter's local check (`gateway.py:291`) refuses before any request, so the case does not show that the tool server refuses a keyless write. That is the path a tool missing from the cache takes (`gateway.py:285-299`: an unknown name skips the local check).
- Fix: add an adapter case with an empty cache, a write tool, and no key. Expect `idempotency_key_required` mapped from the server's tool error by `_tool_error`, and zero effects.

## Checked and found sound

- `BearerAuth` (`remote_auth.py`):
  - Constant-time compare against every token.
  - More than one `Authorization` header is refused.
  - One fixed 401 body; the log carries the method and path only.
  - The header is stripped before the route.
  - It is outermost, so `RequireRun` never runs for an unauthenticated caller.
- `RequireRun` and the remote app:
  - The app has only an explicit route list: no docs, no `/dapr`.
  - It shares state with the loopback proxy, so the remote spends only within an in-flight run.
  - Tests pair the refusals (401, 403, 404) with the allowed control (`test_the_right_token_inside_a_run_is_200_and_charged_to_the_run`).
- `RemoteConnector`:
  - The card URL is pinned to the configured URL, keeping only the JSON-RPC interface.
  - `trust_env=False` and no redirects.
  - The token is not stored as an attribute or shown in `repr`.
  - Setup errors carry the class name only. `raise ... from None` keeps `__context__`, so `DeferredStartup._unreachable` still retries a connect failure and fails fast on a 401 or a bad card.
- `tool_key` canonicalization: the arguments are always a JSON object that ends at `}`, so the `|nonce` boundary cannot be forged by an argument value. Argument order does not change the key.
- Replay, write side: a PoC-4 takeover replays the same client key, so the tool keys match. The code-runner `_running` map shares a run still in flight.
- `DeferredStartup`:
  - The inner lifespan is entered and exited in one task.
  - A shutdown during a retry sleep cancels cleanly.
  - A cancel during `__aenter__` propagates into the generator, so its `finally` blocks run.
  - A failed startup sets `failed` and stops the public server; `main` exits with `STARTUP_FAILURE`.
- `Drain` with three listeners:
  - The proxies bind first, and a failed bind raises `ProxyBindFailed` after stopping the others.
  - The public server stops first, and its lifespan shutdown closes the ports before the proxy and the remote listener are told to exit.
  - A second signal forces every listener out.
- CLI `--remote-proxy-host` checks: one IP, not loopback, not unspecified, a distinct port, the `remote` lane only, and the token variable present.
- Config: `untrusted` requires the `remote` lane, `cloud` requires `spec.trust`, `https`, and no `uds` for remote, and `auth` is refused in the other lanes.
- `workload_a2a/auth.py`: stdlib only, no `chassis` import, constant-time, and the header is stripped.
- code-runner: the child gets `child_env` only, `-I -S`, no shell, and a new session. The workdir is removed with permission repair. Output caps drain the rest, so the child never blocks.
- `public_message` hides the upstream text for every tool code (`test_an_upstream_message_never_reaches_the_workload`).

## Not done

- `core/manifest.py`, `server/manifest.py`, `agent.trust`, `contract-suites interface.py`, and `profiles.py` registry entries: skimmed only.
- `pocs/poc-05-sandboxed/tests/**` and `packages/fake-mcp-server` CLI.
- The test suites were not run. The gate result in the file list (2209 passed) is the orchestrator's, not re-run here. The only run was the grandchild reproduction above.
- Behavior against a real LiteLLM MCP gateway (M4, `_meta` forwarding).
