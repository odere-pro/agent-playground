# echo-typescript

The echo agent in TypeScript, served over A2A by `@a2a-js/sdk` 1.3 (PoC-2). Same prompt (`simplifier-v1`), same events, same error codes as `echo-python`. It proves that `TaskInput`, `Context`, and the event schema of contract v0 carry nothing Python-specific.

## Shape

- `src/handle.ts`: `handle(input, ctx, deps?)`, an async generator of chassis events (`start`, per model call its `delta` events and a `tool_call` per tool it asked for, `metrics` summing every call's usage, `end`; or `error`), `schema_version: "0"` on each. The model call is `POST ${CHASSIS_MODEL_URL}/chat/completions`, streaming, with `stream_options.include_usage`. The timeout is `ctx.budget.timeout_ms`. When `ctx.traceparent` is set, it goes out as the `traceparent` header on every model call and every MCP request, as is; without it, no header.
- `deps` is the third argument, outside the contract and never on the wire: `fetch` (tests stub it), `modelUrl`, `signal` (aborted on cancel), `toolFetch` (the MCP calls; defaults to `fetch`), and `toolUrl`. `ctx` is exactly `context.v0.json`, including its optional `traceparent`.
- `src/a2a_server.ts`: the contract v0 mapping ("Chassis events over A2A") with the v1 changes already decided (items 1 and 4 of "Changes decided for v1 (2026-10-01)"), the executor that enforces the event order, the agent card, and the Express app.
- Chassis JSON crosses A2A as a string. Every event goes out as `JSON.stringify(event)` under `metadata["chassis.event"]` (the artifact's metadata for `delta`); an event with `NaN` or `Infinity` is `workload.bad_event`. The server reads `metadata["chassis.ctx"]` and `metadata["chassis.input"]` as JSON strings, and still reads the v0 object form of both. A string that does not parse to an object is `error {code: "a2a.bad_request"}` and `FAILED` (suggested: the code name). Without `chassis.input`, the input comes from the first text part and the first data part. The native parts are unchanged. So integers stay integers both ways.
- The server passes `ctx` through unchanged. It does not read the `traceparent` HTTP header. The Python reference is `packages/chassis/src/chassis/adapters/a2a/mapping.py` and `server.py`.
- `src/schema.ts`: checks every yielded event against the vendored `schemas/events.v0.json`, a copy of `packages/chassis/schemas/events.v0.json`. Copy it again when that file changes.
- No model key here. `CHASSIS_MODEL_URL` is the chassis's model proxy, which holds the credential. Only in the `remote` lane, when `CHASSIS_API_TOKEN` is set and not empty, every model call and every MCP request carries `Authorization: Bearer <token>`; unset, no `Authorization` header.
- `CHASSIS_MODEL_UDS`, when set, sends the model call over that Unix socket instead of TCP; `CHASSIS_MODEL_URL` then only gives the path and the `Host` header. Node's built-in `fetch` takes no socket path, so this is `udsFetch` in `src/handle.ts`, over `http.request` with `socketPath` (method, headers, string body, abort signal; the body streams). `deps.fetch` still wins when a test sets it.

## The tool loop

`src/tools.ts` is the MCP client, `@modelcontextprotocol/sdk` 1.32.1 (pinned; it takes an injected `fetch`, so tests need no socket). Same loop, event order, and error codes as `echo-python`.

1. At the start of a run, list the chassis's tools over MCP (streamable HTTP, stateless) at `CHASSIS_TOOL_URL` and offer them to the model as OpenAI `tools` (`name`, `description`, the tool's own JSON Schema as `parameters`). If the endpoint cannot be reached, the run goes on without tools; a warning is logged and `toolStats.listFailures` counts it.
2. Stream the model's answer as `delta` events. Streamed `tool_calls` pieces are joined by `index`.
3. If the answer asked for tools: call each over MCP (`tools/call`), yield one `tool_call {call_id, name, arguments, result}` per call, append the assistant message (`content: null`, `tool_calls` with `arguments` as a JSON string) and one `tool` message per result, and call the model again.
4. At most `MAX_TOOL_ROUNDS = 3` rounds of tool calls (suggested). A 4th ask is `error {code: "tool_loop_exceeded", retryable: false}`.

A failed `tools/call` (transport failure or an `isError` result) is `error {code: "tool_error", retryable: false}`. Arguments that are not a JSON object are `error {code: "bad_response"}`. A result that is not an object becomes `{"text": ...}`. Each MCP operation opens its own short session, so nothing stays open while `handle` yields. Every MCP request carries the same `traceparent` and bearer as the model call, and a timeout from `ctx.budget.timeout_ms`.

## Environment

| Variable | Meaning | Default |
| -------- | ------- | ------- |
| `CHASSIS_MODEL_URL` | the chassis's model proxy | `http://127.0.0.1:8090/v1` |
| `CHASSIS_TOOL_URL` | the chassis's tools over MCP | `http://127.0.0.1:8090/mcp` |
| `CHASSIS_API_TOKEN` | remote lane: bearer on every model and MCP call; unset or empty, none | unset |
| `CHASSIS_MODEL_UDS` | send the model call over this Unix socket | unset |
| `HOST`, `PORT` | the bind | `127.0.0.1`, `9000` (suggested) |
| `ALLOW_ANY_HOST` | `1` lets a non-loopback `HOST` bind | unset |
| `UDS` | listen on this Unix socket, no TCP | unset |
| `DRAIN_TIMEOUT_MS` | drain limit on SIGTERM | `30000` (suggested) |
| the variables named by `--require-token-env` and `--previous-token-env` | the inbound tokens | none |

## Run

```bash
npm ci
npm run build
npm start               # A2A on http://127.0.0.1:9000/, card on /.well-known/agent-card.json
```

Flags (the entry point, as `workload-a2a serve`):

- `--require-token-env NAME` puts a bearer check on every request, the agent card included. The token is the value of `$NAME`. A refusal is one fixed 401 with body `{"error":"unauthorized"}` and `www-authenticate: Bearer`. The `authorization` header is removed before the A2A app sees it. A non-loopback `HOST` is allowed with this flag.
- `--previous-token-env NAME` also accepts the value of `$NAME` while a token rotation runs; an unset or blank variable is ignored. Without `--require-token-env` it is a start-up error.
- An unset or blank `$NAME` for `--require-token-env` is a start-up error (exit 2) that names only the variable. The check is in `src/auth.ts`, tested in `test/auth.test.ts`.

`HOST` and `PORT` override the bind (default `127.0.0.1:9000`, suggested). Image: `docker build -t echo-typescript packages/workloads/echo-typescript`. The image binds `127.0.0.1` too. A `HOST` that is not loopback, with no `--require-token-env`, is refused at start (exit 2, the error names ADR-001: the sidecar serves localhost only), the same as `workload_a2a`; set `HOST=0.0.0.0` together with `ALLOW_ANY_HOST=1` only where the chassis reaches it from another network namespace. The guard is `bindHost` in `src/host.ts`, tested in `test/host.test.ts`.

`UDS=<path>` listens on that Unix socket instead and binds no TCP port, so the loopback guard does not apply. A stale socket file at the path is removed first; a path that exists and is not a socket is refused (exit 2). The agent card still names `http://127.0.0.1:<PORT>/`; a client over the socket uses it only for the `Host` header. Tests use this, with `CHASSIS_MODEL_UDS`, to run the whole workload with TCP disabled:

```bash
UDS=/tmp/echo-ts.sock CHASSIS_MODEL_UDS=/tmp/model.sock npm start
curl --unix-socket /tmp/echo-ts.sock http://127.0.0.1:9000/.well-known/agent-card.json
```

## Shutdown

On SIGTERM or SIGINT, `drainOnSignals` (`src/drain.ts`) stops accepting, closes idle keep-alive connections, and lets in-flight `handle` calls finish. It exits 0 when the last connection ends. A call that outlives `DRAIN_TIMEOUT_MS` (milliseconds, suggested default 30000) forces exit 1, and a second signal exits 1 at once. A value that is not a whole number of milliseconds, 0 or more, is refused at start. `test/drain.test.ts` checks this against a real server on a Unix socket; `pocs/poc-04-stateless-scalable/tests/test_workload_drain.py` checks the built workload.

## Test

```bash
npm test                # node --test through tsx; no TCP: fetch stubbed, or a Unix socket in a temp folder
npm run typecheck       # strict TypeScript over src, test, and scripts
npm run fixture         # rewrites packages/chassis/tests/fixtures/events_from_typescript.jsonl
uv run pytest packages/workloads/echo-typescript   # the Python gate runs `npm test` (marked slow)
```

The pytest wrapper skips when `node_modules` is absent. CI does not run `npm ci` for this package yet, so there it skips.

The PoC-2 gate (`make test-poc POC=02`, sockets disabled, Unix sockets allowed) runs the built workload over HTTP: it starts `node dist/src/main.js` with `UDS` and `CHASSIS_MODEL_UDS`, rebuilding `dist` when `src` is newer, and drives it through the chassis's `SidecarConnector` with `spec.engine.uds`. That covers the `EnginePort` suite (`TestEchoTypeScriptEngine`), the response-shape test, and the twin test against `echo_python`, in `pocs/poc-02-two-engines-one-contract/tests/`. They skip only when `node_modules` is absent. One `network` variant of the twin test runs over TCP loopback, and the Compose demo runs it in a container.

The fixture script runs `handle` against a model with no tools, so `npm run fixture` keeps the fixture's one `tool_call` line by hand (`scripts/write-fixture.ts`). `test/agent.test.ts` runs the three benchmark tasks (smoke, simplifier, lookup) with the model and both tools stubbed.
