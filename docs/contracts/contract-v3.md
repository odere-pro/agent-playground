# Contract v3

The written contract after PoC-4: idempotency on every public interface, `/ready` with a reason, graceful shutdown, the config loader with live reload, two new ports (`StatePort` and `EventPort`), `EngineConnector.probe()`, and result events. Every change is additive over [contract v2](contract-v2.md), which stays as the PoC-3 record and still holds for everything this document does not name. Contract v1 still holds for what v2 does not name. Generated schemas: `packages/chassis/schemas/*.json` (`make schemas`), now with `chassis-config.v0.json` and `task-result.v1.json`. Source (under `packages/chassis/src/chassis/`): `server/idempotency.py`, `server/interfaces/serve.py`, `server/interfaces/ids.py`, `server/interfaces/native.py`, `core/inbound.py`, `adapters/openai_compat/inbound.py`, `adapters/anthropic_compat/inbound.py`, `server/readiness.py`, `server/app.py`, `server/lifecycle.py`, `server/cli.py`, `server/config_loader.py`, `server/config.py`, `server/results.py`, `core/results.py`, `ports/state.py`, `ports/events.py`, `ports/config.py`, `ports/engine.py`, `profiles.py`, and the adapters under `adapters/valkey`, `adapters/s3`, `adapters/kafka`, `adapters/dapr`. Status: PoC-4, written 2026-10-01 from the code. The design is [the PoC-4 plan](../plans/2026-10-01-poc-04-stateless-scalable.md). ADR-004 (Dapr or a broker client behind `EventPort`) is not written yet; it waits for the measurements. Where the plan and the code differ, this document describes the code and says so at the end.

Contract v3 is a version of this document, not of the wire. The event schema is still `schema_version: "0"`.

## What did not change

- **The event schema.** `events.v0.json` is unchanged; `schema_version` stays `"0"`, and `SUPPORTED_SCHEMA_VERSIONS` is still `("0",)`. `core/events.py` was last touched before PoC-3.
- **The envelope.** `Request`, `Response`, and `Context` (`core/envelope.py`, `request.v0.json`, `response.v0.json`, `context.v0.json`) are unchanged since contract v2. `idempotency_key` was already on `Request` and `Context` and reaches the workload as before. The config version is already `versions.config`; the replay marker is a header, not a field.
- **`handle`.** Same signature, same wire form, same order rule.
- **The A2A mapping.** `chassis.input`, `chassis.ctx`, `chassis.event`, and `chassis.schema_version` cross as in v1 (`adapters/a2a/mapping.py` is unchanged since contract v2).
- **The lanes.** `inprocess`, `sidecar`, and `remote` (not built) are as in v1. What PoC-4 adds per lane is under "Cost per lane".
- **The interfaces' shapes.** The answer bodies, the status table rows of contract v2, `spec.limits`, `spec.interfaces`, and `/manifest` are as in v2. PoC-4 adds rows and one header.

## Idempotency

018 H-18. A call with a key the client sent runs once per agent and key. A repeat gets the first answer again.

### Which calls are checked

- `ResolvedIds` gains `key_from`: `header`, `body`, or `minted` (`server/interfaces/ids.py`). The rule from contract v2 is unchanged: the `Idempotency-Key` header, else the native body's `idempotency_key`, else minted. An empty string counts as unset.
- Only `header` and `body` are checked, and only when `spec.idempotency.enabled` is true (`Idempotency.applies`). A minted key never repeats, so a call without a key never touches `StatePort`.
- Every interface: native, OpenAI, Anthropic, and MCP. MCP's inner hop forwards `Idempotency-Key` (contract v2), so an MCP call with the header is checked on the inner `/v1/run`.
- Where in the call flow: in `serve`, after `to_request` and `enforce_limits`, before `open_run`. So a refused body or a call over `spec.limits` never claims a key.

### The store key and the fingerprint

- **Store key:** `chassis:idem:v1:<agent.name>:<sha256(idempotency_key) hex>` (`store_key`; suggested: the prefix). Scoped by agent. Hashed, so a long or odd client key cannot shape the store key, and logs hold only the hash.
- **Fingerprint:** sha256 hex of the canonical JSON (`sort_keys`, no spaces) of `{agent, input, context_ref}` (`fingerprint`). Not in it: the ids, `stream`, `budget`, `agent_version`, and the interface. So a retry during a rolling upgrade, a retry that switches to streaming, or the same call through another interface still matches.

### The lease and the takeover

- **Claim:** `set_if_absent(key, running, ttl_s=lease_s)`. The entry is JSON bytes: `{"v": 1, "state": "running", "owner": "<host name>:<uuid4 hex>", "fingerprint": "..."}`.
- **Renew:** while the run is alive, `compare_and_set(key, own, own, ttl_s=lease_s)` every `lease_s / 3`. A renew that finds another value (or none) loses the claim. A renew that hits a store failure, or takes longer than the time left to the fence, keeps the claim until the fence (`chassis.idempotency.renew_failed`).
- **The fence.** The claim is also lost when no renew got through for `0.8 * lease_s` (suggested: `FENCE_FRACTION`), timed on the replica's own monotonic clock from the send time of the last renew that did (or of the claim). The store's lease runs at least `lease_s` from that time, so the fence comes before another replica can take the key over. Counted as `chassis.idempotency.fenced`; every loss as `chassis.idempotency.lease_lost`.
- **A lost claim** cancels its run (the same cancel a client disconnect causes) and answers 409 `idempotency_in_progress`, retryable: in complete mode, and in stream mode before the first `delta` on OpenAI and Anthropic; later in a stream (and on native streams) as one `error` event with that code. It never writes the store and calls no `on_finished` callback, so it publishes no result event. Why 409 and not 503: another call may hold the key now, and a retry with the same key gets that call's result, as for any duplicate in flight.
- **Still possible after a loss:** the side effects the lost run's engine caused before the cancel landed (a model call already sent, a tool already called; write tools in PoC-5 need their own idempotency); a short overlap of two runs when the replica's clock or scheduler stalls past the store's lease (a paused VM); a stream that sent its last event just before the loss keeps that answer, uncached and unpublished.
- **A duplicate** with the same fingerprint polls every `wait_poll_ms` until the entry is `done` (replay), or gone, or the caller's own `budget.timeout_ms` has passed (409 `idempotency_in_progress`). The wait is bounded by this caller's budget, not the first caller's. An entry gone between the claim attempt and the read is polled the same way, inside the same deadline.
- **Takeover:** when the entry is gone (the first run ended with an error and freed it, it was released, or its lease expired after its replica died), the waiting duplicate claims it and runs. Counted as `chassis.idempotency.taken_over`. After a crash, the wait is at most `lease_s`. The run gets only what is left of the caller's `budget.timeout_ms` (never below 500 ms, suggested: `MIN_RUN_MS`), so the client waits about one timeout, not two.
- **An entry of another version** (`v` is not 1, as in a rolling upgrade that changes the entry) is not read and not deleted: the call is refused 409 `idempotency_in_progress`, not retryable, with its own text, counted as `chassis.idempotency.unknown_version` and logged. Why not 503: a 503 is retryable, and every retry would fail until the entry expires (`ttl_s`), an outage per key. Why 409 non-retryable over 422: the key was not misused; it cannot be replayed here.

### What is cached

- **Only a normal end.** `Claim.finish` caches the run when its last event is `end` (`status` `ok`, `retry`, or `fallback`) and the entry fits `max_entry_bytes`. The done entry: `{"v": 1, "state": "done", "fingerprint", "request": <Request>, "events": [<wire events>], "response": <Response>}`, stored with `ttl_s`.
- **Too large to cache:** a result over `max_entry_bytes` stores a small marker for `ttl_s`, `{"v": 1, "state": "too_large", "fingerprint"}`, so the agent never runs twice for one key. A repeat with the same fingerprint is refused 409 `idempotency_in_progress`, not retryable, with a text that says the result was too large to cache; another fingerprint is 422 as usual. Counted as `chassis.idempotency.not_cached{reason="too_large"}`.
- **Never cached:** an `error` event, a run the client left, a run the chassis failed (500 `internal_error`), a run that hit `trace_id_in_use`. The key is freed (`compare_and_set(key, own, None)`), so the next retry runs again. Counted as `chassis.idempotency.not_cached{reason}` (`no_end`, `too_large`). suggested: errors are never cached; a retryable error must be retried, and a non-retryable one costs a rerun at most.
- A finish or release failure is counted (`chassis.idempotency.finish_failed`) and logged, never sent to the client.
- Complete mode finishes after the run. Stream mode finishes from `RunStream`'s `on_close`, after the events are closed.

### The replay

- No run is opened. The engine and the model are not called, nothing is charged, no `RunRecord` is held, and no result event is published. The trace id is not checked, so a replay never gets `trace_id_in_use`.
- The answer is the stored `Response` and events, bound to the original request: the original `request_id`, `trace_id`, `idempotency_key`, and `versions` (`meta.for_request(replay.request)`). So `x-request-id`, `x-trace-id`, and Anthropic's `request-id` name the first run.
- **In this call's interface and mode.** Complete mode is `adapter.complete(response)`; stream mode is `adapter.stream` over the stored events, sent at once. A run first made on native complete can be replayed as an OpenAI stream, and the other way round.
- **`Idempotent-Replayed: true`** on every replayed answer, on native, OpenAI, and Anthropic, complete and stream (`REPLAYED_HEADER`; suggested: the name, as Stripe and the IETF draft use). A first run never carries it. MCP clients cannot see it: the inner hop's headers are not passed back. The tool result is the same envelope.
- **OpenAI `created` is fresh.** `created` is the time of the replay call, not of the first run. Everything else in the body is the stored result. Anthropic `Message` has no time field. Native and MCP send the stored envelope as is.
- Telemetry: a `chassis.idempotent_replay` span (`interface`, `mode`) and `chassis.idempotency.replayed{interface}`.

### Refusals

suggested: every status and text. The texts are fixed (`core/inbound.py`, `PUBLIC_MESSAGES`), on every interface, native included.

| Code | When | Native | OpenAI `type` / `code` | Anthropic `error.type` | `x-should-retry` |
| ---- | ---- | ------ | ---------------------- | ---------------------- | ---------------- |
| `idempotency_conflict` | The key holds another fingerprint, running or done | 422 `{"detail": {code, message}}` | 422 `invalid_request_error` / `idempotency_conflict` | 422 `invalid_request_error` | `false` |
| `idempotency_in_progress` | A run with the same fingerprint is still in flight after this call's `budget.timeout_ms`, or this call's claim was lost mid-run | 409 `{"detail": {code, message}}` | 409 `server_error` / `idempotency_in_progress` | 409 `api_error` | `true` |
| `idempotency_in_progress`, final | The key holds a too-large marker, or an entry of another version; the text says which | 409 `{"detail": {code, message}}` | 409 `server_error` / `idempotency_in_progress` | 409 `api_error` | `false` |
| `state_unavailable` | The store failed (`StateUnavailable`), or holds an entry that cannot be read | 503 `{"detail": str}` | 503 `server_error` / `state_unavailable` | 503 `overloaded_error` | `true` |

- OpenAI's `type` is `invalid_request_error` below 500, except 409, which is `server_error`: the conflict is the server's state, not a bad request.
- Anthropic 409 is `api_error` because anthropic 1.11's error types have no conflict type (`error_type_for`). 422 maps to `invalid_request_error`, 503 to `overloaded_error`.
- Every interface declares its 409, 422, and 503 in the OpenAPI spec, each in its own error shape: OpenAI and Anthropic with their error body (`test_chat_refusal_bodies_match_their_declared_schemas`), native as below.
- Native sends no `x-should-retry`, as in contract v1. Native's 422 is declared as `anyOf` FastAPI's validation shape (`detail` a list) and `{"detail": {code, message}}` (`DetailIssues | DetailCode`). Native's 409 is declared for both `trace_id_in_use` and `idempotency_in_progress`; its 503 for `not_ready` and `state_unavailable`.
- suggested: a keyed call fails closed when the store is down. A call with no key is not affected.

### A client that leaves a complete call

- Native, OpenAI, and Anthropic pass `http.is_disconnected` to `serve`. In complete mode a watcher checks it every 250 ms (`DISCONNECT_POLL_S`, suggested). When the client has left, the run's events are closed: that cancels the A2A task, frees the run's budget in the model proxy, and frees the idempotency key. Counted as `chassis.client_disconnected{interface}` (suggested).
- The answer nobody reads is an empty 499 (nginx's convention).
- Streams already ended this way in contract v2 (`RunStream`); they now free the key too.
- **MCP is not watched.** Its inner ASGI hop does not see the MCP client leave, so an MCP call stays bounded by `budget.timeout_ms` only. A direct native call that sends `x-chassis-interface: mcp` is not watched either.

### Telemetry

suggested: every name. `chassis.idempotency.claimed`, `.refused{code}`, `.waited`, `.taken_over`, `.lease_lost`, `.fenced`, `.renew_failed`, `.unknown_version`, `.not_cached{reason}`, `.finish_failed`, `.replayed{interface}`; the span `chassis.idempotent_replay{interface, mode}`; `chassis.client_disconnected{interface}`; `chassis.idempotency.run_fenced{interface}` (a run whose claim was lost; nothing published). Logs carry the store key (a hash) and the error class, never the client's key or a value.

## `/ready` and `/health`

- **`GET /health`** is unchanged: 200 while the process serves HTTP, draining included. It never looks at the workload, so a hung workload never restarts the chassis.
- **`GET /ready`** is 200 `{"status": "ready"}` only when the lifespan finished, the first config is loaded, the chassis is not draining, and the workload answers its probe. Else 503 `{"status": "not ready", "reason": ...}` with `reason` one of:
  - `starting`: the lifespan has not finished, or the first config is not set.
  - `draining`: SIGTERM was received (see "Shutdown").
  - `workload_unreachable`: the probe failed `failures` times in a row.
- `reason` is a new optional field on the `/ready` body (additive). While draining, calls are still served: `/ready` is what changes, not `serve`'s own `not_ready` check.
- **`EngineConnector.probe() -> bool`** (`ports/engine.py`, new, additive). True or False, never an exception, cheap and bounded. `SidecarConnector`: GET `/.well-known/agent-card.json` over its own client with a 1 s timeout (`PROBE_TIMEOUT_S`); True on 200 only. `InProcessConnector`: True once its client is set up. `FakeEngine`: its `healthy` flag. Every implementer changed in the same package.
- **The monitor** (`ReadinessMonitor`, `server/readiness.py`) probes every `interval_s`, each call bounded by `timeout_s`. A False, a timeout, or an exception is a failure. After `failures` in a row the workload is unhealthy; one success makes it healthy again. It starts healthy, since the engine's `setup` has just reached the workload. `/ready` reads the cached result and never calls the workload itself.
- suggested: `ProbeSettings(interval_s=2.0, timeout_s=1.0, failures=3)`, a `create_app` argument. No CLI flag.

## Shutdown

- **The chassis handles SIGTERM and SIGINT itself** (`server/lifecycle.py`). Both uvicorn servers are `QuietServer`s that install no signal handlers; `serve_pair` installs one with `loop.add_signal_handler`.
- **New flags on `chassis serve`:** `--drain-delay-s` (suggested default 5) and `--drain-timeout-s` (suggested default 30, the default `budget.timeout_ms`). Each must be 0 or more; a negative value exits 2. uvicorn takes the drain timeout in whole seconds, rounded up.
- **The order on the first signal:**
  1. `state.draining = True`. `/ready` answers 503 `draining` at once. New calls are still served.
  2. The `on_drain` hooks run. None is registered in PoC-4 (no event consumer is built).
  3. Sleep `--drain-delay-s`.
  4. The public listener closes. uvicorn waits for in-flight requests, streams included, up to `--drain-timeout-s`, then cancels the rest: those runs close, their A2A tasks are cancelled, and their idempotency keys are freed, so a client retry runs on another replica.
  5. The public lifespan's shutdown: pending result events get up to 5 s (`SHUTDOWN_WAIT_S`, suggested), then the ports close.
  6. Only then the proxy listener closes. In-flight workloads call the model proxy and the tool endpoint until their runs end.
- A second signal exits at once (`force_exit` on both servers). If either server stops on its own (a failed startup), the other stops too.
- **`Connection: close` while draining.** From step 1, every response on the public listener carries `Connection: close` (`CloseWhenDraining`, a pure ASGI middleware; streams are not buffered and still end with `end`), so the client reconnects through the load balancer. The proxy listener never sends it. The limit: a keep-alive connection that stays idle through the whole delay never sees the header, and a request written on it at the instant step 4 closes it gets no answer (httpx does not retry a `POST`). So `--drain-delay-s` must exceed the load balancer's endpoint-removal time plus the clients' idle keep-alive reuse window.
- **The workload side.** `workload-a2a serve --drain-timeout-s N` (suggested default 30) sets uvicorn's `timeout_graceful_shutdown`: on SIGTERM it stops accepting and finishes in-flight `handle` calls, streams included. The TypeScript echo reads `DRAIN_TIMEOUT_MS` (suggested default 30000): in-flight calls finish, then exit 0; a call that outlives it forces exit 1; a bad value is refused at start (exit 2). `chassis/adapters/a2a/server.py` is unchanged, so the ADR-002 mirror rule holds.
- **The order between the containers** is a deploy matter: the workload must get SIGTERM after the chassis has drained (Kubernetes native sidecar, or preStop sleeps). See the plan, section 6, and `pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md`.

## Config

010 H-12.

### Where the config lives

- **The bootstrap file** (`chassis serve --config`) is a whole `ChassisConfig`, as before. It names the profile, the agent, `spec.adapters`, and `spec.engine`, so the chassis can reach the store.
- **With `spec.adapters.config: memory`** (after the profile defaults) there is no store document. The bootstrap is the config; a reload comes from `InMemoryConfig.put` in tests.
- **With any other config adapter** (`minio`, `s3`), the store document `<agent.name>` is a whole `ChassisConfig` too. On S3 it is `<CONFIG_S3_PREFIX><agent.name>.yaml` in `CONFIG_S3_BUCKET` (suggested defaults: `agents/` in `agent-configs`). A missing document fails startup.
- **At start** the store document is validated and checked against the bootstrap with the reload rule below: it may differ from the bootstrap only in reloadable paths. An invalid document, or one that changes a restart-only path, fails startup with `ConfigRejected`. The error names field paths (Pydantic's `loc`), never a value.
- **The schema** is `ChassisConfig`, published as `schemas/chassis-config.v0.json` (`make schemas`). The chassis validates with Pydantic, which the schema is generated from.

### What reloads

`RELOADABLE` (`server/config.py`): `version`, `spec.limits` (all of it), `spec.model.route`, `spec.prompt` (today only `version`), and `spec.idempotency.ttl_s`. A document that changes any other leaf path is refused with `restart_required`, and the last good config stays.

`RESTART_ONLY` names `profile`, `agent`, `spec.adapters`, `spec.engine`, `spec.interfaces`, `spec.events`, `spec.idempotency.enabled`, and `spec.idempotency.lease_s`. It is not the whole list: every path outside `RELOADABLE` is refused, so `spec.idempotency.wait_poll_ms` and `spec.idempotency.max_entry_bytes` are restart-only too.

- **The swap** is one assignment of `state.config`. Every reader that reads it per request sees the new config on its next request. A run keeps the `Context` it opened with. The idempotency layer's spec is swapped in the same step.
- **`version`** of a store document always names its content: `<version>+<hash12>` when the document sets a `version` field, else `<hash12>` alone, where `hash12` is the first 12 hex of the sha256 of the parsed document (sorted-key JSON). Every replica that reads the same document reports the same `versions.config`, and a content change without a bump of `version` still reports a new one. This is a new value form in the existing string field `Versions.config` (the envelope, `/manifest`, the result event's `configversion`), not a schema change. A bootstrap file read without a store keeps its own `version` as written. The S3 version id (or the ETag) only detects change.
- **`/manifest`** is built per request, so it shows the active `versions.config`. **`/openapi.json`** does not: the limits in the `/v1/run` description (`describe_run`) are fixed at start.

### Counters and logs

suggested: the names. `chassis.config.reloaded`, `chassis.config.rejected{reason}` (`invalid`, `restart_required`), `chassis.config.poll_failed{reason}` (the error class). An accepted reload logs the old and new `version`; a refused one logs the field paths.

## Ports

### `StatePort` (new, `ports/state.py`)

```python
class StateUnavailable(RuntimeError): ...

class StatePort(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, *, ttl_s: float | None = None) -> None: ...
    async def set_if_absent(self, key: str, value: bytes, *, ttl_s: float) -> bool: ...
    async def compare_and_set(
        self, key: str, expected: bytes, value: bytes | None, *, ttl_s: float | None = None
    ) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def aclose(self) -> None: ...
```

- Values are bytes; the caller serializes. Keys are at most 512 bytes (`MAX_KEY_BYTES`, suggested). `ttl_s` is seconds, greater than 0. `set_if_absent` always needs a TTL.
- `set_if_absent` and `compare_and_set` are atomic. `compare_and_set` replaces only on a byte-for-byte match; `value=None` deletes; False when the key is absent or differs.
- Every method raises `StateUnavailable` when the store fails, and no other exception. The message holds no credential.
- Fake: `InMemoryState`, defined in `ports/state.py` (the `PortBundle` default) and re-exported from `fakes/state.py`. One per process; two replicas in a test share one object to stand for Valkey.
- Suite: `StatePortContract`, 13 cases. Bound to the fake (`tests/test_state_contract.py`) and Valkey (`tests/integration/test_valkey_state_contract.py`).

### `EventPort` (new, `ports/events.py`)

```python
class EventPort(Protocol):
    async def publish(self, topic: str, event: CloudEvent) -> None: ...
    async def subscribe(
        self, topic: str, handler: EventHandler, *, group: str, max_attempts: int = 3
    ) -> Subscription: ...
    async def aclose(self) -> None: ...
```

- **The envelope** is `CloudEvent`: CloudEvents 1.0, structured mode, JSON, `extra="forbid"`. Attributes: `specversion: "1.0"`, `id`, `source`, `type`, `subject?`, `time`, `datacontenttype: "application/json"`, `dataschema?`, `data`. Extensions, top-level: `traceparent`, `idempotencykey`, `configversion`, `modelroute`, `partitionkey` (the CloudEvents partitioning extension), and on the dead-letter topic only `deadletterreason` and `deadletterattempts`. Wire form: `model_dump_json(exclude_none=True)`, content type `application/cloudevents+json`, the same bytes on every broker. No `cloudevents` SDK.
- **Publish** returns once the broker has the event (Kafka `acks=all`; Dapr a 204). The adapter retries a transient failure, then raises `PublishFailed`.
- **Delivery** is at least once. A handler that returns acknowledges the event. One that raises gets it again, up to `max_attempts` deliveries in total. Then the event goes to `<topic>.dlq` (`DLQ_SUFFIX`, suggested) with `deadletterreason` (the exception class name only, never its message) and `deadletterattempts`. Consumers dedupe by `id`.
- **Order:** events with one `partitionkey` reach one group in publish order.
- **Groups:** each `group` gets every event once.
- `NoEvents` (`spec.adapters.events: none`, the `PortBundle` default): a publish is dropped; `subscribe` raises `RuntimeError("no events adapter")`.
- Fake: `InMemoryBus` (`fakes/events.py`). Suite: `EventPortContract`, 8 cases, every published event validated against the CloudEvents 1.0 JSON Schema in `chassis_contracts/data/cloudevents-1.0.schema.json`. Bound to the fake (`tests/test_events_contract.py`), Kafka, and Dapr (`tests/integration/`; Dapr has two strict xfails, see "Known gaps").

### `ConfigPort` (additive)

- Same shape. New `ConfigUnavailable(RuntimeError)`: the store cannot be reached, or answered with an error other than not found. The message holds no credential. `ConfigNotFound` stays for a missing document.
- The suite allows eventual notification within a `notify_timeout_s` fixture, and gains `test_unsubscribe_stops_notifications` and `test_store_failure_raises_config_unavailable`. Bound to the fake and to S3 (`tests/integration/test_s3_config_contract.py`).

### `EngineConnector` (additive)

`probe() -> bool`, see "`/ready` and `/health`".

### `spec.adapters` names and profile defaults

| Port | Names | `fake` | `local` | `cloud` |
| ---- | ----- | ------ | ------- | ------- |
| `state` | `memory`, `valkey` | `memory` | `valkey` | `valkey` |
| `events` | `none`, `memory`, `kafka`, `dapr` | `none` | `none` | `none` |
| `config` (new names) | `memory`, `minio`, `s3` | `memory` | `minio` | `s3` |

- suggested: `events: none` in every profile; events are opt-in per agent until DEC-1. `none` is not an in-memory double, so `cloud` allows it. `cloud` refuses `memory` for every port, as before.
- `minio` and `s3` are one class (`adapters/s3/config.py`, `S3Config`, on the `minio` SDK).
- Real adapters are loaded lazily, so an unused SDK (valkey, aiokafka, minio) is never imported.

### Environment variables per adapter

Each adapter is built from the environment. A missing required variable is `AdapterNotAvailable` at start. No credential is logged or put in a URL.

| Adapter | Required | Optional (suggested defaults) |
| ------- | -------- | ----------------------------- |
| `valkey` | `VALKEY_URL` (`valkey://` or `valkeys://` for TLS) | `VALKEY_USERNAME`, `VALKEY_PASSWORD` |
| `minio`, `s3` | `CONFIG_S3_ENDPOINT` (host:port), `CONFIG_S3_ACCESS_KEY`, `CONFIG_S3_SECRET_KEY` | `CONFIG_S3_SECURE` (`true`), `CONFIG_S3_REGION`, `CONFIG_S3_BUCKET` (`agent-configs`), `CONFIG_S3_PREFIX` (`agents/`), `CONFIG_POLL_INTERVAL_S` (5) |
| `kafka` | `KAFKA_BOOTSTRAP_SERVERS` | `KAFKA_SECURITY_PROTOCOL` (`PLAINTEXT`; SASL arrives with 020 X-8) |
| `dapr` | `DAPR_API_TOKEN` (the chassis to daprd), `APP_API_TOKEN` (daprd to the chassis) | `DAPR_HTTP_ENDPOINT` (`http://127.0.0.1:3500`), `DAPR_PUBSUB_NAME` (`pubsub`), `DAPR_MAX_RETRIES` |

- **Dapr subscriptions** are served on the proxy app only (localhost), never on the public port: `GET /dapr/subscribe` and `POST /dapr/events/{topic}`, mounted when the events port has `inbound_routes()`. A call without the right `dapr-api-token` (the `APP_API_TOKEN`) is 401. The handler answers `SUCCESS`, `RETRY` on an exception, or `DROP` for a body that is not a CloudEvent.
- Kafka: producer `acks="all"`, `enable_idempotence=True`, `linger_ms=5`; publish back-off 0.1 s then 0.5 s; consumer `enable_auto_commit=False`, `auto_offset_reset="earliest"`, commit after the handler returns or after the dead-letter publish; delivery back-off 0.2 s then 1 s. suggested: every value.

## Result events

019 H-17. With `spec.events.result_events: true` (default `false`), the chassis publishes one CloudEvent after each run (`server/results.py`).

- **Topics:** `agents.task.completed.v1` after a run with `status` `ok`, `retry`, or `fallback`; `agents.task.failed.v1` after a run with `status: error`. The topic's own major is `v1`.
- **The CloudEvent:** `id` is fixed per run and type: the hex of `uuid5(EVENT_ID_NAMESPACE, "<type>:<request_id>")` (`event_id`; suggested: the namespace, `uuid5(NAMESPACE_URL, "urn:chassis:agents.task")`), so a consumer dedupes a second delivery of one result by `id`; `source: /agents/<agent>`; `type` the topic; `subject` the `request_id`; `time` UTC now; `dataschema: task-result.v1.json`; `traceparent` the run's; `idempotencykey` and `partitionkey` the sha256 hex of the run's `idempotency_key` (`key_hash`, the hash `store_key` uses, without the agent prefix), never the key itself, so one key's events keep their order and a reader of the topic cannot learn the key; `configversion` and `modelroute` from the response's `versions`. suggested: `source`, `subject`, `dataschema`, and the partition key.
- **The payload** is `TaskResult` (`core/results.py`, `schemas/task-result.v1.json`): `{agent, agent_version, request_id, idempotency_key, status, input, output, usage, versions}`, `extra="forbid"`. `idempotency_key` holds the key's sha256 hex, not the key (the field keeps its name; the schema is unchanged). `usage` is the run's token counts as the `metrics` event carries them.
- **Never in the request's path.** The publish runs in a background task. A slow or broken broker never delays or changes the answer.
- **When nothing is published:** `result_events` is off; the events adapter is `none`; the run did not finish (the client left, the stream ended early, the chassis failed); the call was a replay; the call was refused before a run (not ready, limits, an idempotency refusal, `trace_id_in_use`).
- **Counters** (suggested: the names): `chassis.events.published{topic}`, `chassis.events.publish_failed{topic}` (with one warning log of the ids and the exception class, never the payload), `chassis.events.publish_abandoned` (still pending when the 5 s shutdown wait ran out).
- `spec.events` is restart-only.

## Config reference

New `spec` blocks; suggested: every name and default.

| Field | Default | Reloads |
| ----- | ------- | ------- |
| `spec.adapters.state` | per profile | No |
| `spec.adapters.events` | `none` | No |
| `spec.idempotency.enabled` | `true` | No |
| `spec.idempotency.ttl_s` | 86400 (one day); greater than 0 | Yes |
| `spec.idempotency.lease_s` | 5; greater than 0 | No |
| `spec.idempotency.wait_poll_ms` | 100; at least 1 | No |
| `spec.idempotency.max_entry_bytes` | 1048576; at least 1 | No |
| `spec.events.result_events` | `false` | No |
| `spec.events.consume` | unset (null); the only value accepted today | No. In the schema, but a non-null value is refused at load: event-triggered runs are not built (019 H-17) |

## Cost per lane

- **`inprocess`.** Idempotency, `/ready`, the config loader, and result events sit before the connector, so they cost the same in every lane. The probe is free (the client is in memory). Shutdown has one process; the proxy-last order still protects the model proxy.
- **`sidecar`.** The probe is one localhost GET of the agent card every 2 s. Shutdown needs the container order (workload last) and `--drain-timeout-s` on the workload. A keyed call costs about two store round trips more (claim, finish) plus one renew every `lease_s / 3`; a call without a key costs none.
- **`remote` (not built).** The probe would cross the network and need the per-remote credential. The drain order between replicas and a remote workload is not defined yet.

## Contract suites

| Suite | New or changed | Bound to |
| ----- | -------------- | -------- |
| `StatePortContract` | New, 13 cases | `InMemoryState`, `ValkeyState` |
| `EventPortContract` | New, 8 cases | `InMemoryBus`, `KafkaEvents`, `DaprEvents` (2 strict xfails) |
| `ConfigPortContract` | Eventual notification, `ConfigUnavailable`, unsubscribe | `InMemoryConfig`, `S3Config` |

The v1 and v2 suites are unchanged. PoC scenarios: `pocs/poc-04-stateless-scalable/tests/`.

## Changes from v2 and why

All additive; none changes `events.v0.json`, `request.v0.json`, `response.v0.json`, or `context.v0.json`, so `schema_version` stays `"0"`.

1. **Idempotency on every interface,** with `Idempotent-Replayed: true` and three refusal codes. Why: PoC-4 runs several replicas behind a load balancer, and a client retry must not run the agent twice (018 H-18).
2. **`/ready` with `reason`, and `EngineConnector.probe()`.** Why: a load balancer must stop sending to a draining replica or one whose workload hangs, while `/health` must never restart the chassis for the workload's fault.
3. **Graceful shutdown and the drain flags.** Why: a rolling update must not cut in-flight runs, and the workload must outlive the chassis's drain.
4. **The config loader and live reload.** Why: 010 H-12; the agent config lives in the store, and replicas must agree on `versions.config`.
5. **`StatePort` and `EventPort`.** Why: the chassis keeps no state of its own (the PoC-4 question), and 019 H-17 names the event port.
6. **Result events and `task-result.v1.json`.** Why: 019 H-17; other services learn a run ended without polling.
7. **A complete call ends when its client leaves (499).** Why: PoC-3 debt; an abandoned call kept spending tokens and held its idempotency key.

### Where the code differs from the plan's text

This document follows the code. These are the places where [the PoC-4 plan](../plans/2026-10-01-poc-04-stateless-scalable.md) says something else:

- **The done entry also stores the original `request`** (plan, section 4, lists only `fingerprint`, `events`, `response`). A replay needs it to bind the answer to the original ids.
- **"The stored `Response` is the original result, byte for byte":** true for native and MCP. OpenAI's `created` is the time of the replay call.
- **OpenAI error types** (plan, section 4): the plan wrote 409 as `server_error` / `conflict_error` and 503 as `server_error` / `overloaded_error`. The code sends `type: server_error` with `code` set to the chassis code (`idempotency_in_progress`, `state_unavailable`), as every other row of the v2 status table does.
- **Anthropic 409** is `api_error`; the plan did not name it. anthropic 1.11 has no conflict error type.
- **Native refusals carry the fixed text** from `PUBLIC_MESSAGES`, not a message of their own.
- **The OpenAI and Anthropic routes declare 409 and 422** (closed after the PoC-4 review). FastAPI had declared its own 422 (`HTTPValidationError`) on both, which matched any error body; it is replaced by each format's error model. `packages/chassis/tests/test_idempotency_serve.py::test_chat_refusal_bodies_match_their_declared_schemas` checks 409, 422, and 503 per format. The MCP tool is built from `/v1/run` only, so its schema did not change.
- **`taken_over` counts every claim after a wait,** including after the first run ended with an error and freed the key, not only after a crash.
- **`state_unavailable` also covers an unreadable entry** (bad JSON, unknown `v`), not only `StateUnavailable`.
- **The 499** for a client that left is not in the plan; the plan said only that the run is cancelled.
- **`InProcessConnector.probe()`** is True once its client is set up, not always True.
- **`--drain-timeout-s` is rounded up to whole seconds** for uvicorn.
- **`/ready` `starting`** also covers "the first config is not set", and the monitor starts healthy.
- **Reload:** `spec.prompt` reloads as a whole (it holds only `version` today; the plan named `spec.prompt.version`). `wait_poll_ms` and `max_entry_bytes` are restart-only; the plan did not say.
- **The start check:** the store document is checked against the bootstrap with the reload rule, so it may differ only in reloadable paths. The plan said "check the restart-only fields".
- **`version`** is the content hash of the parsed document (sorted-key JSON), not of the document bytes. A bootstrap file with no store still hashes its text, as before.
- **`chassis.config.poll_failed`** carries a `reason` label (the error class).
- **Dapr `max_attempts`** is checked against the `DAPR_MAX_RETRIES` environment variable, not by reading `resiliency.yaml`. A mismatch is logged, not enforced.
- **`InMemoryState`** is defined in `ports/state.py` and re-exported from `fakes/state.py`, because `PortBundle` defaults to it.
- **Event-triggered runs (P9) were not built.** `spec.events.consume` is in the schema, but a non-null value is refused at load with a message naming 019 H-17 (`test_events_consume_is_refused_until_event_triggered_runs_exist` in `packages/chassis/tests/test_server.py`); nothing subscribes; the `Interface` literal has no `event`; no `on_drain` hook is registered. The suites alone exercise `subscribe`.

### Known gaps

| Gap | Where | Owner |
| --- | ----- | ----- |
| Dapr dead-letters the event as it was published, with no `deadletterreason` or `deadletterattempts`, after its own `maxRetries + 1` deliveries, not `max_attempts` | Strict xfail in `packages/chassis/tests/integration/test_dapr_events_contract.py` | ADR-004 (`chassis-architect`) |
| Dapr has one consumer group per app id; a second group needs a second daprd | Strict xfail, same file | ADR-004 |
| daprd reads `GET /dapr/subscribe` once at start; a later subscription is not seen until daprd restarts | `adapters/dapr/events.py` | ADR-004 |
| The Python workloads report a refused budget as `http_429` with `budget_exhausted` only in the message | `pocs/poc-04-stateless-scalable/tests/test_timeout_budget_per_engine.py` | 017 H-4, PoC-8 |
| The TypeScript echo has no tool loop, so its budget refusal is untestable | Same file, a strict xfail with the reason | 017 H-4, PoC-8 |
| The OpenAPI limits in `describe_run` are fixed at start; `/openapi.json` and the MCP tool description show the start values after a reload | `server/interfaces/native.py` | suggested: accept for PoC-4; 051 H-13 |
| The INFO `config reloaded` log line does not show in the container log | `server/config_loader.py`, log setup | PoC-7 (`observability-expert`) |
| `KafkaEvents`: a failed dead-letter publish ends that subscription's consumer task | `adapters/kafka/events.py` | 019 H-17 (`developer`) |
| The vendored CloudEvents schema was transcribed from memory; it must be diffed against upstream cloudevents/spec v1.0.2 before it counts as vendored | `packages/contract-suites/src/chassis_contracts/data/cloudevents-1.0.schema.json` | `chassis-architect` |
| `pgsty/minio` community images replace the official MinIO image, which no longer pulls | `deploy/compose/docker-compose.scale.yaml`, `deploy/compose/SECURITY.md` | `platform-security` |
| Closed: `spec.events.consume` was accepted but did nothing. A non-null value is now refused at load until event-triggered runs exist | `server/config.py`; `tests/test_server.py::test_events_consume_is_refused_until_event_triggered_runs_exist` | 019 H-17 builds the consumer |
| Closed: the OpenAI and Anthropic routes did not declare 409 and 422 | `server/interfaces/openai.py`, `anthropic.py`; `tests/test_idempotency_serve.py::test_chat_refusal_bodies_match_their_declared_schemas` | Done |
| Closed: the TypeScript echo never pruned its A2A task store. `PruningTaskStore` forgets a task 3 s (`PRUNE_DELAY_MS`, suggested) after its final save; it reaches the SDK's internal bucket because `TaskStore` has no delete | `packages/workloads/echo-typescript/src/a2a_server.ts`; `test/prune.test.ts` | Recheck on an `@a2a-js/sdk` bump |
