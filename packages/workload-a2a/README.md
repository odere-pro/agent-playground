# workload-a2a

The template A2A server a Python workload ships with (ADR-002, option A). It wraps a workload's `handle(input, ctx)` and serves it over A2A, so the chassis's `sidecar` connector can reach it on localhost. It never imports the chassis: the workload's environment holds this package, a2a-sdk, and its own dependencies, nothing else.

## Shape

- `mapping.py`: chassis events to A2A updates and back. A byte-for-byte copy of `chassis.adapters.a2a.mapping`; a test fails when the two differ.
- `server.py`: `HandleExecutor`, `build_agent_card`, `build_app`, `build_server`, `serve`. Every event `handle` yields is validated with `jsonschema` against the vendored `schemas/events.v0.json` and gets the schema's defaults, so the wire JSON equals what the chassis's own copy produces. Error codes match the chassis copy: `workload.bad_event`, `workload.bad_order`, `workload.no_end`, `workload.exception`, `a2a.unsupported_schema_version`, `a2a.bad_request` (a `chassis.ctx` or `chassis.input` that is not a JSON object).
- `schemas/events.v0.json`: a copy of `packages/chassis/schemas/events.v0.json`; a test fails when the two differ. `SUPPORTED_SCHEMA_VERSIONS` comes from its `schema_version` consts.
- The task store is pruned: `PruningRequestHandler` deletes each task once it is terminal, so a long-running sidecar does not keep every task in memory.
- Localhost only: the server binds `127.0.0.1` by default and refuses a host that is not loopback unless you pass `allow_any_host` (ADR-001).

## Use

From code:

```python
from workload_a2a.server import build_agent_card, serve
from my_workload import handle

serve(handle, build_agent_card(name="my-workload", version="0.1.0", url="http://127.0.0.1:9000"), port=9000)
```

From the command line (the console script, or `python -m workload_a2a`):

```bash
workload-a2a serve --handle echo_python:handle --port 9000
workload-a2a serve --handle echo_python:handle --uds /tmp/echo.sock   # Unix socket, tests and local runs
```

Flags: `--host` (default `127.0.0.1`), `--allow-any-host`, `--name`, `--version`, `--description`, `--log-level`, `--drain-timeout-s` (suggested default 30). The chassis side points at it with `spec.engine.connector: sidecar` and `spec.engine.url: http://127.0.0.1:9000` (`packages/chassis/configs/sidecar.yaml`).

## Remote lane: bearer token and rotation

`--require-token-env NAME` puts a bearer check on every request, the agent card included. A missing or wrong token gets one fixed 401. `--previous-token-env NAME` also accepts the token in `$NAME`, compared in constant time against both tokens. A variable that is unset or empty is ignored without error, so one manifest works before, during, and after a rotation. The flag without `--require-token-env` is a start-up error. The `authorization` header is removed before the app sees it, and no token is logged.

The workload uses one token both ways: `CHASSIS_API_TOKEN` is what it sends to the chassis proxy (as its API key) and what it requires on its own server. The chassis sends only its current token (`spec.engine.auth.token_env`) and accepts both on its remote listener (`token_env` and `previous_token_env`). Each variable is read at start, so every change below needs a pod restart.

Rotation that never produces a 401. Old token `O`, new token `N`. The Secret `remote-<name>-token` holds the key `token` and an optional key `previous-token` (map it with `optional: true`). Each side accepts the set `{current, previous}`, so the "previous" slot carries `N` in step 1.

1. Accept `N` everywhere, send `O` still. Secret: `token` = `O`, `previous-token` = `N`. Chassis: `token_env` (for example `REMOTE_TOKEN`) = `O`, `previous_token_env` (`REMOTE_TOKEN_PREVIOUS`) = `N`. Workload: `CHASSIS_API_TOKEN` = `O`, previous variable (`CHASSIS_API_TOKEN_PREVIOUS`, passed as `--previous-token-env`) = `N`. Restart the chassis pods and the workload pods, in any order. Wait until every pod of both is ready.
2. Swap what is sent. Secret: `token` = `N`, `previous-token` = `O`. Same variable names, new values: both sides now send `N` and still accept `O`. Restart both, in any order, one pod at a time. Wait until every pod of both is ready.
3. Drop `O`. Secret: delete `previous-token`. The previous variables become unset and are ignored. Restart both, in any order.

Why it holds. Until step 2 starts, every pod on both sides accepts `N`. During step 2 a pod that has swapped sends `N`, accepted by all. A pod that has not swapped sends `O`, and every pod still accepts `O`. In step 3 nobody sends `O` any more. The same holds in both directions: chassis to workload A2A calls (the workload checks `{current, previous}`) and workload to chassis proxy calls (the chassis listener checks `{token_env, previous_token_env}`). The rule is that no side starts a step before every pod of both sides finished the one before. The chassis side needs nothing new: its connector reads only `token_env` and its listener already takes `previous_token_env`.

## Shutdown

On SIGTERM or SIGINT the server stops accepting, closes idle keep-alive connections, and lets in-flight `handle` streams finish for up to `--drain-timeout-s` seconds. Then it exits 0. A second signal exits at once. `DrainingServer` in `server.py` keeps sse-starlette from ending open streams on the first signal. The chassis drains first and the workload gets SIGTERM after it (`docs/guides/poc-04-how-it-works.md`, section "Graceful shutdown of a pair").

## Test

`uv run pytest packages/workload-a2a`. The server runs on uvicorn over a Unix socket in a background task; no TCP, no key. One test compares the stream with the chassis's own template server and skips when the chassis is not installed.
