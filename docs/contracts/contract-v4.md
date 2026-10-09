# Contract v4

The written contract after PoC-5: the `remote` lane with its per-remote credential, `spec.trust` and the admission check, the remote proxy listener, the uncorrelated token cap, `ToolPort` write mode with its error codes, and `agent.trust` in `/manifest`. Every change is additive over [contract v3](contract-v3.md), which stays as the PoC-4 record and still holds for everything this document does not name. Contracts v2 and v1 still hold for what v3 does not name. Generated schemas: `packages/chassis/schemas/*.json` (`make schemas`); `chassis-config.v0.json` and `manifest.v0.json` gain optional fields and keep their major. Source (under `packages/chassis/src/chassis/`): `server/config.py`, `server/remote_auth.py`, `server/model_proxy.py`, `server/tool_endpoint.py`, `server/cli.py`, `server/manifest.py`, `core/manifest.py`, `core/inbound.py`, `ports/tool.py`, `ports/events.py`, `adapters/a2a/remote.py`, `adapters/mcp/server.py`, `adapters/mcp/gateway.py`, and `profiles.py`; outside the chassis, `packages/workload-a2a/src/workload_a2a/auth.py` and `cli.py`, and `deploy/kind/poc05/admission/`. Status: PoC-5, written 2026-10-02 from the code, and checked against the code again on 2026-10-09 for the close (T30): the gateway's tool naming and refusal texts, the `/mcp` rule for a name it does not list, the CLI's `--host`, and the known gaps from the cluster run. The design is [the PoC-5 plan](../plans/2026-10-02-poc-05-sandboxed.md), section 3. The decisions are [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md). Where the plan and the code differ, this document describes the code and says so at the end.

Contract v4 is a version of this document, not of the wire. The event schema is still `schema_version: "0"`.

## What did not change

Everything contract v3 accepts is still accepted. A PoC-1 to PoC-4 workload, config, or caller works unchanged.

- **The event schema.** `events.v0.json` is unchanged; `schema_version` stays `"0"`, and `SUPPORTED_SCHEMA_VERSIONS` is still `("0",)`.
- **The envelope.** `Request`, `Response`, and `Context` (`request.v0.json`, `response.v0.json`, `context.v0.json`) are unchanged. The remote's bearer token is never in `ctx`, in `input`, or in A2A metadata.
- **`handle`.** Same signature, same wire form, same order rule.
- **The A2A mapping.** `adapters/a2a/mapping.py` is unchanged and stays byte for byte equal in `chassis` and `workload-a2a`. The `remote` lane carries the same A2A messages as `sidecar`. The only difference is an `Authorization: Bearer` header on the transport, which the mapping never sees.
- **The `sidecar` and `inprocess` lanes.** Same connectors, same config. `spec.engine.auth` is refused in both, so an old config cannot pick it up by accident.
- **The public interfaces.** Native, OpenAI, Anthropic, and MCP: same routes, bodies, status rows, and refusal texts as v3. The v3 `PUBLIC_MESSAGES` entries are unchanged; PoC-5 adds five tool entries.
- **Idempotency, `/ready`, shutdown, the config loader, and result events.** As in v3. `RELOADABLE` gains one path inside `spec.limits`, which already reloaded as a whole.
- **`StatePort`, `ConfigPort`, `EngineConnector`, and `EventPort`.** Unchanged. `RemoteConnector` implements `EngineConnector` as it is.
- **`ToolPort` callers.** `idempotency_key` is a keyword with a default of `None`, so every existing call to `call(name, arguments)` still works for a read-only tool.
- **`spec.adapters` names.** As in v3, plus `tools: mcp`.
- **The loopback proxy listener** (`--proxy-host`, `--proxy-port`, suggested 8090). Same routes, same loopback-only bind. It now binds before the public listener (see "The remote listener").

## Trust

Plan section 2.5, ADR-005. `spec.trust` says whether the workload's code is trusted. It is checked twice: in the chassis config at load, and by the admission policy on the cluster.

- **Values:** `trusted` or `untrusted` (`Trust`, `server/config.py`). Restart-only.
- **Default:** `trusted` in the `fake` and `local` profiles (`DEFAULT_TRUST`, suggested), so every v3 config still loads. The `cloud` profile has no default. A `cloud` config that does not set `spec.trust` is refused: `spec.trust is required in profile 'cloud': set it to trusted or untrusted`. The check reads `model_fields_set`, so writing the default value out counts as set.
- **The rule:** `untrusted` needs `spec.engine.connector: remote`. Any other lane is refused at load: `spec.trust: untrusted needs spec.engine.connector: remote (got '<lane>')`. This is the trust rule in the chassis, checked before anything starts.
- `trusted` with `remote` is allowed. A third-party image or a managed runtime may be trusted and still remote.
- `spec.trust` is shown in `/manifest` as `agent.trust`. It is not a security control by itself; the admission policy is.
- Tests: `pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py`.

## The `remote` connector and `spec.engine.auth`

Plan sections 2.1 and 2.2. ADR-001 item 7: one A2A client, a different URL. `RemoteConnector` (`adapters/a2a/remote.py`) is `A2AConnector` with a bearer token and a remote URL. Everything after `setup` (the mapping, the deadline, the cancel, the span, the `traceparent`) is the `sidecar` code.

### `spec.engine` for `remote`

| Field | Rule |
| ----- | ---- |
| `connector` | `remote` |
| `url` | Required. `http(s)://host:port[/path]`: a host and a port, no user info, no query, no fragment. `https` in the `cloud` profile. The error names the field and the broken part, never the URL itself |
| `auth` | Required for `remote`. Refused for `sidecar` and `inprocess`: `spec.engine.auth is for spec.engine.connector: remote only` |
| `uds` | Optional, for tests and local runs: a Unix socket the requests go over. `url` still fills `Host` and the path. Refused for `remote` in `cloud` |
| `probe_timeout_s` | Float, greater than 0. Default 2.0 (suggested; `PROBE_TIMEOUT_S`). Passed to the connector only in the `remote` lane, so the other lanes see the same `setup` mapping as in v3 |

### `spec.engine.auth` (`EngineAuthSpec`)

```yaml
spec:
  engine:
    connector: remote
    url: https://agent-x.poc05-remote.svc:8000
    auth:
      scheme: bearer                         # the only value today
      token_env: REMOTE_AGENT_X_TOKEN        # required
      previous_token_env: REMOTE_AGENT_X_TOKEN_PREVIOUS   # optional, null by default
```

- suggested: every name. `scheme` leaves room for `sigv4` and `google` (PoC-6); only `bearer` is accepted now. `extra="forbid"`.
- `token_env` and `previous_token_env` name environment variables. They must match `^[A-Z_][A-Z0-9_]*$`. The config never holds a token.
- **The connector** reads `os.environ[token_env]` at `setup`. A missing or empty variable is a `LookupError` that names the variable, never a value. It sends `token_env`'s token only.
- **The remote listener** accepts `token_env`'s token and, when `previous_token_env` is set and not empty, that one too. So a rotation can swap either side first.
- **Every request carries the token:** the card fetch, every message, the cancel, and the probe.
- **The token goes to the configured host only.** The httpx clients ignore the proxy and TLS environment (`trust_env=False`) and never follow a redirect. The card's interface URL is never followed: the connector keeps only the JSON-RPC interface and sets its URL to `spec.engine.url`. A card without one is refused.
- The token is never kept as an attribute, logged, put in a span or an error, or shown in `repr`.
- **`probe()`** GETs the agent card with the token over its own client. True on 200 only; False on any other status, a timeout, or a transport error. It never raises.

### The remote workload's side (`workload-a2a`)

Plan section 2.4. `workload-a2a serve --require-token-env NAME` puts a bearer check on every request, the agent card included (`workload_a2a/auth.py`, standard library only, no `chassis` import, per ADR-002). `--previous-token-env NAME` also accepts a second token during a rotation; it needs `--require-token-env`, and an unset or empty variable is ignored. A missing or wrong token gets one fixed 401, `{"error":"unauthorized"}` with `www-authenticate: Bearer`, and the app is not called. After a good check the `authorization` header is removed from the scope, because a2a-sdk logs request headers at DEBUG. A host that is not loopback needs `--require-token-env` (or the older `--allow-any-host`).

## The remote listener

Plan section 2.3. ADR-001 item 8. A remote workload runs in another pod, so it cannot reach the loopback proxy. `chassis serve --remote-proxy-host <pod IP>` adds a third listener on `--remote-proxy-port` (8091, suggested; `DEFAULT_REMOTE_PROXY_PORT`). The app is `create_remote_proxy_app` (`server/remote_auth.py`).

### What it serves

An explicit route list, nothing else:

| Route | What |
| ----- | ---- |
| `POST /v1/chat/completions` | The model proxy (`model_proxy_router`), the same router as on the loopback listener |
| `/mcp` | The tool endpoint (`mount_tool_endpoint`), the same MCP server as on the loopback listener |

- Every other path is 404. No `/dapr/*`, no docs, no OpenAPI document (`server/remote_auth.py:197-206`).
- It shares the public app's `state` (the ports, `runs`, the uncorrelated cap). It has no lifespan of its own.
- A websocket is closed with 1008 before accept. Any scope that is not `http` or `lifespan` is dropped. Both middlewares do this, so a websocket never reaches a route (`server/remote_auth.py:91-96`, `135-137`, `176-178`).

### Refusals

Two pure ASGI middlewares, outermost first. suggested: every code, type, and text.

| Status | `error.code` | `error.type` | `error.message` | When |
| ------ | ------------ | ------------ | --------------- | ---- |
| 401 | `remote_unauthenticated` | `authentication_error` | `missing or invalid bearer token` | `BearerAuth`: no `Authorization` header, a scheme other than `Bearer`, an empty token, a wrong token, or more than one `Authorization` header. Sends `www-authenticate: Bearer` |
| 403 | `run_required` | `permission_error` | `the traceparent names no run in flight` | `RequireRun`: the token was good, but the `traceparent` is missing, invalid, or names no run in `state.runs` |
| 404 | (FastAPI's) | | | The path is not in the route list. Only after both checks pass |

- The body is one fixed JSON object, `{"error": {code, type, message}}`, the same bytes every time. The caller learns nothing about which token or which run.
- **The token check** compares the presented token with every accepted token (`hmac.compare_digest`, and no early exit), so the time does not say which one matched. On success the `Authorization` header is removed before the route sees it.
- **The run check** means a remote spends tokens and calls tools only inside a run the chassis opened, under that run's budget.
- **Counters** (suggested: the names): `chassis.remote.auth_failed{reason}` with `reason` `missing` or `wrong`; `chassis.remote.run_required`. A 401 also writes one warning log with the reason, the method, and the path only, never the header (`server/remote_auth.py:155-160`). A 403 is counted and not logged (`server/remote_auth.py:182-186`).
- **Order.** `BearerAuth` runs first, then `RequireRun` (`server/remote_auth.py:207-209`). So a call with no token gets 401 even when its `traceparent` names a run, and the run check never runs for a caller without the token.
- **`/mcp` on this listener** never sees a call outside a run: `RequireRun` answers 403 first. The `/mcp` refusal of a write outside a run (`idempotency_key_required`, below) shows on the loopback listener only.
- A model or tool call that passes both checks answers as on the loopback listener, including 429 `budget_exhausted` and the tool errors below.

### The CLI

- `--remote-proxy-host` must be one IP address (the pod IP). A host name, `localhost` included, loopback, and wildcards (`0.0.0.0`, `::`) are refused. It is refused unless `spec.engine.connector` is `remote` and the variable in `spec.engine.auth.token_env` is set. `--remote-proxy-port` must differ from `--port` and `--proxy-port`. A bad flag exits 2 with the reason (`server/cli.py:120-147`).
- The `remote` lane does not need `--remote-proxy-host`. Without it the chassis serves a remote with no listener for the remote's model and tool calls. The CLI has no rule that ties the two together (`server/cli.py:166-167`).
- **`--host` (the public listener) has no CLI rule.** `chassis serve --host 0.0.0.0` is accepted (`server/cli.py:79`, and `parse_args`, lines 150-168, checks `--proxy-host` only). Only `--remote-proxy-host` refuses a wildcard. The pod-IP bind of the public port is held by the manifests under `deploy/kind/poc05/` and their static test (`pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py::test_h13_the_public_port_binds_the_pod_ip_and_the_proxy_loopback`). See "Known gaps".
- **Bind order (H14).** The proxy listeners bind first (`server/cli.py:224-235`, `Drain.serve`). A taken proxy port exits 3 with a message naming the address, before the public listener binds and before anything reaches the workload. Then the public lifespan runs in the background: `/health` answers at once and `/ready` is 503 `starting` until the workload is reachable, for up to `--startup-wait-s` (120, suggested). Past that, exit 3.
- On shutdown the proxy listeners stop last, as in v3.

## The uncorrelated cap

Plan section 2.12, H16. Since PoC-2, a model call on the proxy whose `traceparent` names no run in flight is still served, counted as `chassis.model_calls_uncorrelated`. PoC-5 caps those calls per replica (`UncorrelatedCap`, `server/model_proxy.py`). A correlated call never touches the cap.

- **The limit:** `spec.limits.uncorrelated_tokens_per_minute` (L). Integer, at least 0. Default 20000 (suggested). `0` refuses every uncorrelated call. Reloadable: it is read from `state.config` on each admission.
- **The window:** a fixed window of 60 s (`WINDOW_S`), not a sliding one. Per replica, in process, by design.
- **The worst case:** the call's `max_tokens`, or 1024 when it sets none (`DEFAULT_UNCORRELATED_MAX_TOKENS`, suggested; the epic gives no value). The 1024 is a ceiling, and it is itself capped at L: the default worst case is `max(1, min(1024, L))` (`server/model_proxy.py:108`, `144-148`). A `max_tokens` below 1 counts as 1. A `max_tokens` the caller sets is not capped at 1024: it is admitted when it fits L. The worst case is forwarded upstream as the call's `max_tokens`.
- **Counted first:** every call that names no run in flight counts `chassis.model_calls_uncorrelated` and logs a warning before the cap is checked, so a refused call is counted twice: once as uncorrelated, once as refused (`server/model_proxy.py:479-490`).
- **Admission:** the call is let in only if spent plus reserved plus its worst case is at most L. The check and the reservation are one step under a lock.
- **Refusal:** 429, before the model is called, counted as `chassis.model_calls_refused`. Body: `{"error": {"message", "type": "budget_exhausted", "code": "budget_exhausted", "retryable"}}`. The message is `uncorrelated model calls are capped at <L> tokens per minute`, plus `; this call asks for <worst>` when L is above 0 and the worst case alone is over L (`server/model_proxy.py:277-289`). `retryable` is `true` when the call would fit an empty window, and `false` for L = 0 and for a call whose worst case alone is over L. For `stream: true` the refusal is one error frame, then `[DONE]`.
- **Settle:** the reservation is replaced by the call's usage (input plus output tokens), charged to the window in which it settles. A reservation still held when the window rolls carries over.
- **No usage:** a call that reached the model and ends with no usage (an upstream error, a stream the client left, a cancel) is charged its whole reservation. A stream the client left after a usage frame is charged that last usage (`_Hold.release`, `server/model_proxy.py:365-375`). A refused call, or a stream whose body never started, is charged nothing.
- **Not the run path.** This rule is the cap's only. A stream inside a run that the client closes early gives its reservation back to the run uncharged (`_Hold.release`, the `record.release` branch). That is a PoC-4 debt, 004 G-2; see "Known gaps".
- **The bound:** the tokens charged in one window are at most L plus the prompt tokens of the calls that settle in it. `max_tokens` cannot bound a prompt; LiteLLM's key budget is the hard cap for that. After a reload lowers L, calls in flight keep their reservations.
- The remote listener shares the cap through `app.state.uncorrelated_cap`, so the two listeners spend one budget.

## Ports

### `ToolPort` (additive, `ports/tool.py`)

Plan section 2.6, 054 H-16.

```python
class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    read_only: bool = True

class ToolPort(Protocol):
    name: str
    def list_tools(self) -> Sequence[ToolDefinition]: ...
    async def call(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> ToolResult: ...
```

- **New:** the keyword `idempotency_key`. `ToolDefinition` and `ToolResult` are unchanged.
- **Mode `write`** is `read_only: false` (MCP's `readOnlyHint: false`). The gateway adapter treats a tool with no `readOnlyHint` as a write tool (fail safe).
- **A write needs `idempotency_key`.** Without one, the port raises `ToolError("idempotency_key_required")` before anything runs. With one, the write has at most one effect per key, and a repeat returns the first result, even with other arguments.
- **A read-only call** takes no key and may repeat.
- **A failure the tool itself reports** is `ToolResult(is_error=True)`, never a `ToolError`.

### `ToolError` codes and their public messages

`ToolError(code, message, *, retryable=False)`. The `message` is for the log and the span only. What reaches the workload is the fixed text for the code from `PUBLIC_MESSAGES` (`core/inbound.py`, `public_message(code)`). suggested: every code and text.

| Code | When | `retryable` | Public message |
| ---- | ---- | ----------- | -------------- |
| `unknown_tool` | No tool by that name. In the gateway adapter: a JSON-RPC error `-32601`, a JSON-RPC error whose text says so, or a tool error result whose text says so (LiteLLM: `Error: Tool '<name>' not found`) | `false` | `"no such tool"` |
| `bad_arguments` | The arguments fail the tool's `parameters` (the adapter checks required fields, top-level types, and extra fields when `additionalProperties` is false, before any request); a JSON-RPC error `-32602`; a tool error result whose text is a validation error; or, on a `/mcp` write, `_meta.idempotency_key` is not a string of 1 to 256 characters | `false` | `"the tool arguments do not match the tool's input schema"` |
| `idempotency_key_required` (new) | A write tool called without a key; on `/mcp`, a write, or a name the port does not list, with no run in flight; a tool error result whose text starts `idempotency_key_required` (the tool server got no key, see "Tool names") | `false` | `"this is a write tool; call it inside a run"` |
| `tool_denied` (new) | The gateway answered 401 or 403 (the key is not valid or not allowed), or a JSON-RPC error or tool error result whose text is LiteLLM's allow-list refusal: `Error: Tool '<name>' is not allowed for your key/team ...` or `User not allowed to call this tool.` | `false` | `"this tool is not allowed for this service"` |
| `tool_unavailable` (new) | No answer (a connect error or a timeout), or the gateway answered 5xx, 408, or 429 | `true`. Any other unexpected status is also `tool_unavailable`, with `retryable: false` | `"the tool could not be reached; retry later"` |

Sources: the codes and their `retryable` in `ports/tool.py:36-55`; the texts in `core/inbound.py:245-250`; the `/mcp` checks in `adapters/mcp/server.py:80-94` and `145-159`; the gateway adapter in `adapters/mcp/gateway.py:70-83` (the patterns) and `181-208`, `328-371` (the mapping).

**How the gateway adapter maps an answer** (`McpGatewayTools.call`), in order. The first match wins.

1. Before any request, for a tool it lists under that name: a write tool with no key is `idempotency_key_required`; arguments that fail the listed schema are `bad_arguments` (`gateway.py:336-341`).
2. The call's own JSON-RPC error: `-32601` or an unknown-tool text is `unknown_tool`, an allow-list refusal text (the same LiteLLM texts as step 4) is `tool_denied`, `-32602` is `bad_arguments`. It wins over any HTTP status the session saw (`gateway.py:181-199`).
3. An HTTP 401 or 403 is `tool_denied`. A 5xx, 408, or 429 is `tool_unavailable`, retryable. Any other status of 400 or more is `tool_unavailable`, not retryable. No answer is `tool_unavailable`, retryable (`gateway.py:200-208`). The status of the session-close `DELETE` is never read: a gateway in stateless mode may refuse it (`gateway.py:176-178`).
4. A tool error result (HTTP 200, `isError: true`) whose first text block is a refusal maps by its text: unknown tool, then denied, then `idempotency_key_required`, then a validation error (`gateway.py:359-370`). The patterns match LiteLLM v1.103.0's texts, taken on kind (`pocs/poc-05-sandboxed/notes/2026-10-02-bring-up.md`, section 3 and "Requests"). Test: `packages/chassis/tests/test_tool_gateway_contract.py::test_the_gateway_s_refusal_texts_map_to_their_codes`.
5. Any other tool error result stays `ToolResult(is_error=True)`: the tool's own failure (`gateway.py:371`).

`ToolError.message` is fixed text with the tool name, never the upstream body. LiteLLM puts no allow-list status on the wire: a tool off the key's list is HTTP 200 with an error result, so step 4, not step 3, is how the code sees it on kind.

### The tool endpoint (`/mcp`, both proxy listeners)

- **A `ToolError`** becomes an MCP tool error result (`isError: true`), never a transport failure. Its text is the public message. Its structured content is `{"code", "message", "retryable"}`, with `message` the public message (`adapters/mcp/server.py`, `_error`).
- **The tool key.** The workload does not pick the port's key. For a write inside a run, the endpoint derives it: `tk1:` plus the first 40 hex of the sha256 of `<run key>|<tool>|<canonical JSON of the arguments>|<nonce>` (`tool_key`; suggested: the prefix and the length). The run key is the run's idempotency scope when `spec.idempotency.enabled` is true, so a replayed run sends the same tool keys again and the tool server dedupes the writes; else it is the `request_id`. The nonce is the workload's optional MCP `_meta.idempotency_key`, mixed in, never sent raw.
- **A write with no run in flight** (no `traceparent`, or an unknown one) is refused with `idempotency_key_required` before the port is called.
- **The nonce is read on a write only.** A read-only call never reads `_meta.idempotency_key`, so a bad one is not refused there (`adapters/mcp/server.py:149-151`).
- `/mcp` lists what the port lists now, read on every request. Before the lifespan builds the MCP server, `/mcp` is 503.
- **A name the port does not list** is still sent to the port, as a write tool with an open schema: the list is a cache, and the gateway decides (`adapters/mcp/server.py:208-216`). So outside a run, any unlisted name, an unknown one or a read tool by a name the port does not list, is refused with `idempotency_key_required`, not `unknown_tool`. Inside a run it gets a derived key and goes to the port, which answers `unknown_tool` or the tool's result. Test: `packages/chassis/tests/test_tool_endpoint_gateway_lifecycle.py::test_a_call_to_a_tool_no_longer_listed_still_goes_to_the_port`.

### `spec.adapters.tools: mcp`

`McpGatewayTools` (`adapters/mcp/gateway.py`), an MCP client to LiteLLM's MCP gateway. The gateway's per-key allow-list is the hard limit; the adapter adds none of its own.

| Adapter | Required | Optional (suggested defaults) |
| ------- | -------- | ----------------------------- |
| `mcp` | `LITELLM_MCP_URL`, `LITELLM_API_KEY` (the service's own virtual key) | `LITELLM_MCP_AUTH_HEADER` (`Authorization`) |

- suggested: 30 s per call (`DEFAULT_TIMEOUT_S`), a list refresh every 60 s (`TOOLS_REFRESH_S`). A failed refresh keeps the last list and counts `chassis.tools.refresh_failed`.
- The key goes as `_meta.idempotency_key`, and also as the `idempotency_key` argument when the tool is listed under the called name, is a write tool, and its schema declares that property (`gateway.py:342-345`).
- A tool with no `readOnlyHint` is listed as a write tool (`gateway.py:139`).
- No proxy variable or redirect can carry the key elsewhere (`trust_env=False`, no redirects). The key is never logged, in an error, or in `repr`.
- Profile defaults: `fake` uses `fake` (`default_tools`); `local` and `cloud` use `mcp`. `cloud` refuses `fake` and `memory`, as for every port.
- Suite: `ToolPortContract` (`chassis_contracts/tool.py`) adds the write cases: listed as write, refused without a key with no effect, one effect per key, a reused key with other arguments returns the first result, two keys are two effects, a tool outside the allow-list is not listed and not called, unavailable is retryable. Bound to the fake and to `McpGatewayTools` over `packages/fake-mcp-server` (`packages/chassis/tests/test_tool_gateway_contract.py`).

#### Tool names and the key on LiteLLM's gateway

Found on kind (bring-up note, section 3, "Requests", and "Kind tier, 2026-10-08"; T19). These are the gateway's behavior, not a chassis rule.

- **Names are `<server>-<tool>`.** The gateway lists `fake_tools-glossary_lookup`, `fake_tools-note_write`, `code_runner-run_python`. The chassis passes the names on unchanged: `/mcp` lists the same names (`pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md`). A workload calls the listed name.
- **`_meta` is not forwarded.** The tool server never sees `_meta.idempotency_key`. Only the argument reaches it. So a write tool that does not declare `idempotency_key` in its schema never gets the key, and a third-party tool server may ignore the argument. The code runner's result cache is in memory.
- **A bare name.** The gateway also answers a bare tool name (`glossary_lookup`). The adapter does not list that name, so it skips its own checks and does not add the key argument (`gateway.py:331-345`). A read tool called by its bare name works. A write tool called by its bare name reaches the server with no key, and the server's refusal maps to `idempotency_key_required` (step 4 above). Through `/mcp`, the same call outside a run is refused before the port (see "The tool endpoint"). See "Known gaps".

### `EventPort` (unchanged; stays broker-agnostic)

Decision 2026-10-02: the queue adapter is broker-agnostic, and PoC-5 runs no broker.

- The port is `EventPort` (`ports/events.py`), as in v3. The envelope is CloudEvents 1.0, structured mode, JSON (`CloudEvent`, `application/cloudevents+json`), the same bytes on every broker. No `cloudevents` SDK.
- The adapter is chosen by `spec.adapters.events` only: `none`, `memory`, `kafka`, `dapr`. Never in code. `none` stays the default in every profile.
- **No broker in PoC-5.** The PoC-5 agent configs under `deploy/kind/poc05` set `events: none`.
- `chassis.ports.events` and `chassis.core` import no broker client, directly or through any module they import.
- Every events adapter other than `none` binds `EventPortContract`: `memory` offline, `kafka` and `dapr` in `packages/chassis/tests/integration/`.
- A broker the chassis has no client for (RabbitMQ, GCP Pub/Sub, AWS SNS/SQS) fits as a Dapr pub/sub component behind the `dapr` adapter, with no chassis change. A direct client for one of them is a new adapter that binds the same suite (060 H-23).
- Tests: `pocs/poc-05-sandboxed/tests/test_poc05_events_agnostic.py` checks all of the above on every commit.
- ADR-004 (a broker client in the chassis, not Dapr) is written and still Proposed. The broker product stays 001 DEC-1's.

## `/manifest`

`ManifestAgent` gains `trust: "trusted" | "untrusted"`, default `trusted` (`core/manifest.py`). It is `spec.trust`. Manifest v0, an optional field, so `MANIFEST_VERSION` stays `"0"`. It is shown to callers. It is not a security control by itself.

```json
{"agent": {"name": "echo", "version": "0.1.0", "trust": "untrusted"}, "...": "..."}
```

## Admission

Plan section 2.5, ADR-005 decisions 4 and 5, the security review (`pocs/poc-05-sandboxed/notes/2026-10-02-review-security.md`, F1 to F3). A built-in `ValidatingAdmissionPolicy`, `agent-trust-rule` (suggested), in `deploy/kind/poc05/admission/`: `policy.yaml`, `binding.yaml`, `params.yaml`, `rbac.yaml`, `fixtures/`. No controller, no memory.

### Scope and params

- **Binding:** `validationActions: [Deny, Audit]`, on namespaces labeled `agents.platform/admission: enforce` (suggested). `failurePolicy: Fail`. `parameterNotFoundAction: Deny`, so missing params deny everything.
- **Matched kinds:** Pods (and `pods/ephemeralcontainers`), ReplicationControllers, Deployments, ReplicaSets, StatefulSets, DaemonSets, Jobs, CronJobs, and agent-sandbox `Sandbox`, on create and update. A bad Deployment is refused at apply time, not when it makes pods.
- **Params:** the ConfigMap `agent-trust-params` in `agent-platform-system` (suggested). Only `agents.platform:platform-admins` may edit it (`rbac.yaml`). Keys: `registryPrefix` (kind: `kind.local/agent-platform/`), `imageSource` (`pull-never` on kind, `digest` on a cloud cluster), `trustedRepositories` (comma-separated; on kind the two echo workloads; the probe workload is not on it on purpose). suggested: every name and value.
- **Pod shape** is read from the pod, never from a label the submitter writes alone:
  - `chassis`: at least one chassis image (`registryPrefix` + `chassis`). With another image next to it, the pod is in the `sidecar` lane.
  - `remote`: no chassis image, and `agents.platform/lane: remote` or `agents.platform/trust: untrusted` (suggested: untrusted code without a chassis is always held to the remote shape).
  - `tool`: anything else.
- **The trust label is a one-way signal.** `untrusted` is always believed. `trusted` counts only when the platform-owned `trustedRepositories` agree (rule 4).
- The images checked are every container, init container, and ephemeral container.

### Rules

Each rule has its own message, which starts `trust rule <n>:`. The kind test matches the text.

| Rule | Refuses |
| ---- | ------- |
| 0 | The params lack `registryPrefix`, `trustedRepositories`, or an `imageSource` of `pull-never` or `digest`. Fails closed |
| 1 | A pod without `agents.platform/trust` set to `trusted` or `untrusted` |
| 2 | An `untrusted` pod in the `sidecar` lane |
| 3 | Outside the remote lane, an image that does not start with `registryPrefix`. The message lists the images |
| 4 | A `trusted` sidecar-lane pod with a workload image (inside the prefix) whose repository is not in `trustedRepositories`. Images outside the prefix are rule 3's, so each rejected fixture breaks one rule only |
| 5 | A remote pod without `runtimeClassName: gvisor` and `automountServiceAccountToken: false`, or with a projected service account token, or with any Secret reference that is not a `remote-<name>-token` |
| 6a | More than one chassis container in a pod |
| 6b | A chassis container that sets `command`: the image's entrypoint is `chassis serve` |
| 6c | Outside the remote lane, a container other than the chassis that references a Secret (env, `envFrom`, or a mount of a secret or projected-secret volume) |
| 7a | A pod whose shape does not match its namespace's `agents.platform/pod-shape` label (`chassis` for `poc05-agents`, `remote` for `poc05-remote`, `tool` for `poc05-tools`) |
| 7b | `agents.platform/role: chassis` on a pod that does not run the chassis image |
| 7c | A remote pod that takes another remote's token: the only `remote-*-token` it may take is `<app.kubernetes.io/name>-token` |
| 7d | A tool pod without `runtimeClassName: gvisor` and `automountServiceAccountToken: false` |
| 8 | Outside the remote lane, an image not from the cluster's own store. `pull-never`: every container sets `imagePullPolicy: Never`. `digest`: every image is pinned by `@sha256` |

### Rules 3 and 5 are stricter than the plan

- **Rule 3.** The plan checks the registry prefix "in a `sidecar`-lane pod". The policy checks it **outside the remote lane**: sidecar pods, chassis-only pods, and tool pods. So a pod without a chassis cannot run a third-party image either. It also covers ephemeral containers, so `kubectl debug` with an outside image is refused.
- **Rule 5.** The plan refuses "an `envFrom` or volume from a Secret other than `remote-<name>-token`". The policy refuses **every** Secret reference that does not match `^remote-[a-z0-9]([-a-z0-9]*[a-z0-9])?-token$`: `env[].valueFrom.secretKeyRef`, `envFrom[].secretRef`, `secret` volumes, and `secret` sources inside `projected` volumes. It also refuses a `projected` volume with a `serviceAccountToken` source, which `automountServiceAccountToken: false` alone does not stop. And a remote pod is any non-chassis pod labeled `untrusted`, not only one labeled `agents.platform/lane: remote`, so dropping the lane label does not dodge the rule. Rule 7c then pins the token to the pod's own name.

### Tests

- Offline: `pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py` checks a Python model of the CEL and the syntax it can see. Each rule has a rejected fixture and an admitted twin that differs only in the field the rule checks.
- On kind: `test_poc05_kind_admission.py` applies each fixture with `--dry-run=server` as the submitter (as the deployer for fixtures in `poc05-agents`, `admission/rbac.yaml`) and asserts the outcome and the message. Part of the CEL has run on kind: the API server type-checked the policy with no warning (`status.typeChecking` is `{}`), and the rule-1 canary was refused while its twin was admitted ([bring-up note](../../pocs/poc-05-sandboxed/notes/2026-10-02-bring-up.md), section 9, lines 128-129). The per-rule kind test ran on 2026-10-08: 51 passed, every rejected fixture refused by `agent-trust-rule` with its rule message and every admitted twin admitted ([sidecar suite note](../../pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md), "Results per H id", the admission row and the command tails).
- CI: criterion 1's kind half is not in CI yet. The remote-lane workflow (`.github/workflows/remote-lane.yml`) runs by hand only, until the gVisor x86_64 sum and the kind and kubectl sums are pinned and the remote kind test files exist.

## Config reference

New fields; suggested: every name and default. `chassis-config.v0.json` keeps its major: every new field is optional or has a default outside `cloud`.

| Field | Default | Reloads |
| ----- | ------- | ------- |
| `spec.trust` | `trusted` in `fake` and `local`; none in `cloud` (required) | No |
| `spec.engine.connector` | adds `remote` | No |
| `spec.engine.url` | required for `remote` | No |
| `spec.engine.auth.scheme` | `bearer` (the only value) | No |
| `spec.engine.auth.token_env` | required when `auth` is set | No |
| `spec.engine.auth.previous_token_env` | `null` | No |
| `spec.engine.probe_timeout_s` | 2.0; greater than 0 | No |
| `spec.limits.uncorrelated_tokens_per_minute` | 20000; at least 0 | Yes |
| `spec.adapters.tools` | adds `mcp`; `mcp` in `local` and `cloud` | No |

`RELOADABLE` is unchanged as a list: `spec.limits` already reloads as a whole. `RESTART_ONLY` gains `spec.trust`; `spec.engine` was already there.

## Cost per lane

- **`inprocess`.** None. `spec.trust: untrusted` and `spec.engine.auth` are refused here.
- **`sidecar`.** The bind-order change and the uncorrelated cap. No new hop, no new credential. `spec.engine.auth` is refused here.
- **`remote`.** One network hop each way: A2A to the remote, and model and tool calls back to the remote listener. One bearer check per request on each side (`hmac.compare_digest`, microseconds). One readiness probe across the network, with the token, bounded by `probe_timeout_s`. One Kubernetes Secret per remote (`remote-<name>-token`), shared by the chassis and that remote only. gVisor's overhead on the remote pod (measured in the PoC-5 gVisor note, exit criterion 9). The drain order between replicas and a remote workload is still not defined.

## Contract suites

| Suite | New or changed | Bound to |
| ----- | -------------- | -------- |
| `ToolPortContract` | Write cases: write listed as write, refused without a key with no effect, one effect per key, a reused key returns the first result, two keys two effects, outside the allow-list not listed and not called, unavailable is retryable | The fake tools; `McpGatewayTools` over `packages/fake-mcp-server` (`packages/chassis/tests/test_tool_gateway_contract.py`) |
| `LaneContract` | Runs over `remote` too | `inprocess`, `sidecar`, and `remote`, the last two on Unix sockets with the token on (`pocs/poc-05-sandboxed/tests/test_poc05_remote_lane_contract.py`) |

`StatePortContract`, `EventPortContract`, and `ConfigPortContract` are unchanged from v3. PoC scenarios: `pocs/poc-05-sandboxed/tests/`.

## Changes from v3 and why

All additive; none changes `events.v0.json`, `request.v0.json`, `response.v0.json`, or `context.v0.json`, so `schema_version` stays `"0"`.

1. **The `remote` connector and `spec.engine.auth`.** Why: ADR-001 items 7 and 8; untrusted code runs in another pod, reached over A2A with a per-remote credential.
2. **`spec.trust` and the admission policy.** Why: 055 CH-6; untrusted code must never share a pod with the chassis, and the label alone must not make code trusted.
3. **The remote listener with 401 `remote_unauthenticated` and 403 `run_required`.** Why: a remote in another pod needs the model and the tools, but only with its own credential and only inside a run the chassis opened (H17 to H20, H30).
4. **The uncorrelated cap.** Why: H16; a workload that drops the `traceparent` could spend tokens outside any run's budget.
5. **`ToolPort` write mode, `idempotency_key`, and three error codes.** Why: 054 H-16; a retried or replayed run must not repeat a write, and a refused tool must say why without leaking an upstream body.
6. **`agent.trust` in `/manifest`.** Why: a caller can see which lane's rules apply.
7. **The proxy listeners bind first.** Why: H14; a workload must never find the proxy port free and take it.

Checked on 2026-10-09: the gateway's refusal-text mapping (added 2026-10-08), the `/mcp` rule for an unlisted name, and the `_redact` fix are additive. They map more upstream answers to the PoC-5 codes, and change no field, route, or v3 text. One behavior is new and was not in v3, so it is flagged, not counted as a break: on `/mcp`, outside a run, a name the port does not list now answers `idempotency_key_required`. v2 and v3 did not say what an unlisted name answers.

**Rejected:** a new `schema_version` (nothing on the wire changes); the token in `ctx` or in A2A metadata (it would cross into `handle` and into logs; a transport header is never seen by the mapping).

### Where the code differs from the plan's text

This document follows the code. These are the places where [the PoC-5 plan](../plans/2026-10-02-poc-05-sandboxed.md) says something else:

- **Paths.** The plan writes `kind5/` and `poc/tests/`. The code is in `deploy/kind/poc05/` and `pocs/poc-05-sandboxed/tests/`.
- **The registry prefix** is `kind.local/agent-platform/`, not `agent-platform/` (security review F1: `agent-platform/` is short for `docker.io/agent-platform/`, a namespace anyone may own).
- **Admission has rules 0 to 8, not 1 to 5.** Rule 0 (params complete, fail closed), 6a to 6c, 7a to 7d, and 8 come from the security review (F1 to F3, F15).
- **Rules 3 and 5 are stricter** (see "Admission"): rule 3 holds outside the remote lane, not only in the `sidecar` lane; rule 5 covers every Secret reference, projected Secrets, and projected service account tokens, and a remote pod is also any non-chassis pod labeled `untrusted`.
- **The binding is `[Deny, Audit]` with `parameterNotFoundAction: Deny`.** The plan named the two modes but not the missing-params case.
- **The tool error's structured content is `{code, message, retryable}`.** The plan wrote `{code, retryable}`. `message` is the public message, never the error's own.
- **The public tool messages live in `core/inbound.py` (`PUBLIC_MESSAGES`),** next to the v3 run error texts. `ports/tool.py` names the codes and their `retryable` only.
- **`tool_unavailable` also covers 408 and 429** (retryable), and any other unexpected gateway status (not retryable). The plan said "did not answer, timed out, or failed with a 5xx".
- **A tool with no `readOnlyHint` is a write tool** in the gateway adapter (fail safe). The plan did not say.
- **`RELOADABLE` did not gain a path.** The plan said "only the new limit joins `RELOADABLE`". `spec.limits` already reloads as a whole, so the new field reloads with no change to the list.
- **`workload-a2a`** still accepts `--allow-any-host` for a host that is not loopback. The plan said a host that is not loopback needs `--require-token-env`. Either flag is accepted.
- **The remote listener refuses a websocket** with close code 1008 before accept, and drops any other scope that is not `http` or `lifespan`. The plan listed the routes only.

## Open questions

| Question | Where | Owner |
| -------- | ----- | ----- |
| **A model refusal reaches the remote as a 500.** When LiteLLM refuses a route the key does not list, the adapter raises `ModelError("http_401", ..., retryable=False)`. The model proxy sends every non-retryable `ModelError` as 500 (`status_code=502 if exc.retryable else 500`), so the remote gets HTTP 500 with `{"error": {"type": "model_error", "code": "http_401", "retryable": false}}`. A 500 says the chassis broke; here the caller asked for something it may not have. `http_401` also names the chassis's upstream hop, not the caller's problem. Proposal, not built: map an upstream 401 or 403 to 403 with `code: model_route_denied` and `type: permission_error`, `retryable: false`, and a fixed message. Keep the 500 for real upstream failures. Additive: a new code on the proxy listeners only; no event schema change | `pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py::test_h29_a_route_outside_the_key_is_refused_and_an_in_scope_call_is_200` (asserts `== 500` and `http_401`, so a change to this question fails the test); `server/model_proxy.py` | `chassis-architect` (suggested: PoC-6, with the remote engines) |
| **H11 has no evidence.** No broker runs on kind in PoC-5. Recorded as an exception | `pocs/poc-05-sandboxed/notes/2026-10-02-h11-queue-exception.md` | `platform-security`, closing in 020 X-8 |

## Known gaps

| Gap | Where | Owner |
| --- | ----- | ----- |
| Closed on 2026-10-08: the per-rule admission test had not run on kind. `test_poc05_kind_admission.py` then ran there, 51 passed | `pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md` | PoC-5 kind pass (`tester`) |
| `trustedRepositories` is a hand-kept ConfigMap, not registry metadata or a signature | `deploy/kind/poc05/admission/params.yaml` | 025 H-10 (`platform-security`) |
| The drain order between chassis replicas and a remote workload is not defined | "Cost per lane" | `chassis-architect` |
| The uncorrelated cap is per replica; N replicas allow N times L | `server/model_proxy.py` | suggested: accept for PoC-5; LiteLLM's key budget is the cluster-wide cap |
| A model stream inside a run that the client closes early gives its reservation back to the run uncharged. An uncorrelated stream that started is charged | `_Hold.release`, `server/model_proxy.py:365-375` | 004 G-2 (PoC-4 debt). H31 waits on it |
| A write tool called by its bare name, not the gateway's `<server>-<tool>`, gets `idempotency_key_required`: the adapter adds the key argument only for a tool it lists under the called name, and the gateway drops `_meta`. Read tools work by bare name | `adapters/mcp/gateway.py:331-345`; bring-up note, "Requests" and "Kind tier, 2026-10-08" | 054 H-16 (`chassis-architect`) |
| A write tool whose schema does not declare `idempotency_key` never gets the key through LiteLLM's gateway, and a third-party tool server may ignore it. The code runner's result cache is in memory and a restart forgets it | `adapters/mcp/gateway.py:342-345`; bring-up note, section 3 | 054 H-16 |
| On `/mcp`, outside a run, a name the port does not list is refused as `idempotency_key_required`, not `unknown_tool`, even for an unknown name or a read tool | `adapters/mcp/server.py:208-216` | 054 H-16 (`chassis-architect`) |
| `chassis serve --host 0.0.0.0` has no CLI rule. Only `--remote-proxy-host` refuses a wildcard. The pod-IP bind of the public port is held by the manifests and their static test | `server/cli.py:79`, `150-168`; `test_poc05_hostile_offline.py::test_h13_the_public_port_binds_the_pod_ip_and_the_proxy_loopback` | 026 CH-4 (B6) |
| Closed in the chassis: LiteLLM's 401 body echoes the last 4 characters of a refused key (`Received API Key = sk-...<4>`) and its hash (`Key Hash (Token) = ...`). `_redact` now strips both, so they no longer reach `ModelError.message`, the 500 body on the proxy listeners, the `error` event, or `/v1/run`. Still open outside the chassis: LiteLLM logs the same line at INFO | `adapters/litellm/client.py:89-102`; `packages/chassis/tests/test_litellm.py::test_401_litellm_echo_of_key_suffix_and_hash_is_redacted` | 026 CH-4 (`platform-security`); 022 H-6's key-leak canary should also look for the suffix and hash |
| The v3 known gaps still hold, unless closed there | [contract v3](contract-v3.md), "Known gaps" | As listed there |
