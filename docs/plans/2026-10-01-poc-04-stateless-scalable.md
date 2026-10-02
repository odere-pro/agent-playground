# PoC-4 stateless and scalable: design and work plan

Status: in progress
Date: 2026-10-01
Author: `chassis-architect`. The orchestrator assigns the packages in "Work breakdown".
Source: `docs/planning/poc/004-PoC-4-stateless-scalable.md`. Tracking: `pocs/poc-04-stateless-scalable/README.md`.
Backlog previewed: [010 H-12](../planning/issues/010-H-12-config-loader.md), [018 H-18](../planning/issues/018-H-18-idempotency.md), [019 H-17](../planning/issues/019-H-17-event-port.md), [057 H-19](../planning/issues/057-H-19-stateless-check-ci.md), [017 H-4](../planning/issues/017-H-4-harness-features.md) (timeout and budget only), [024 CH-3](../planning/issues/024-CH-3-helm-library-chart.md).

**Outcome (2026-10-01).** Built, measured, and reviewed; not yet closed. 10 of 11 exit criteria have evidence; throughput growth (criterion 4) is flagged, with 4 of 8 steps shown on this host. The record is the [PoC-4 README](../../pocs/poc-04-stateless-scalable/README.md). What the code does, where it differs from this plan, and the known gaps: [contract v3](../contracts/contract-v3.md). The events decision: [ADR-004](../planning/adr/004-events-through-a-broker-client.md), a broker client in the chassis, proposed. The design text below is left as written.

This plan decides every open shape in PoC-4, so a developer does not have to ask. Values the epic does not give are marked `suggested:`. Where this plan and the code disagree later, the code wins and `contract-v3.md` records the difference.

## Summary of decisions

- **Two new ports.** `StatePort` (bytes by key, TTL, `set_if_absent`, `compare_and_set`) and `EventPort` (`publish`, `subscribe`, CloudEvents 1.0 structured JSON, at-least-once, retries, a dead-letter topic). Each has an in-memory fake and a contract suite in `packages/contract-suites`.
- **Real adapters.** `valkey` (valkey-py, `valkey.asyncio`), `kafka` (aiokafka), `dapr` (httpx to the daprd sidecar), and `minio`/`s3` for `ConfigPort` (the `minio` Python SDK, one class registered under both names). Each is imported only under `chassis.adapters`, built from environment variables, and loaded lazily, so an unused SDK costs no memory.
- **Config loader.** A bootstrap file names the profile and the adapters. The agent config document lives in the store, is validated by `ChassisConfig` (the published schema `chassis-config.v0.json` is generated from it), and is polled. Only `version`, `spec.limits`, `spec.model.route`, `spec.prompt`, and `spec.idempotency.ttl_s` reload. Any other change is refused and the last good config stays.
- **Idempotency.** Only for a key the client sent. The claim is a lease in `StatePort`, renewed while the run is alive. A finished run (`end` event) is cached with its events and its envelope. A repeat replays it on any interface and in either mode, with the header `Idempotent-Replayed: true`. Same key and different input is 422 `idempotency_conflict`. A duplicate in flight waits for the first result, up to its own `budget.timeout_ms`, then 409 `idempotency_in_progress`.
- **Shutdown.** The chassis handles SIGTERM itself, not uvicorn. It sets `/ready` to 503, keeps serving for a drain delay, stops accepting, waits for in-flight runs, then stops the proxy listener last. The workload finishes its in-flight `handle` calls (Python and TypeScript).
- **Liveness.** `/health` never looks at the workload. `/ready` is false while starting, while draining, and when the workload probe fails 3 times in a row. The workload container gets an exec liveness probe on the agent card.
- **Deploy.** `docker-compose.scale.yaml` (project `poc04`) with Traefik (file provider, active health checks on `/ready`) in front of explicit pairs `chassis-N` and `workload-N`, N = 1 to 4, chosen by profiles. kind manifests with kustomize for two variants: native sidecar and preStop delay.
- **Load test.** Locust, run by `uv run --with`, never a workspace dependency. The fake model server is reached directly, with no LiteLLM in the measured path.
- **Contract.** The event schema, the envelope, `handle`, and the A2A mapping do not change: `schema_version` stays `"0"`. The public interfaces gain one header, three error codes, and a `/ready` reason. These are additive and go into `contract-v3.md`, a new version of the document, not of the wire. ADR-004 (Dapr or a broker client) is written at the end, with the measurements.

## 1. `StatePort`

**File:** `packages/chassis/src/chassis/ports/state.py`. No network code, no product SDK.

```python
class StateUnavailable(RuntimeError):
    """The store cannot be reached or answered with an error. The message holds no credential."""

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

- Values are `bytes`. The caller serializes. Keys are `str`, at most 512 bytes (suggested).
- `ttl_s` is seconds, a float, greater than 0. `None` on `set` means no expiry. `set_if_absent` always needs a TTL: a claim that never expires would block a key forever after a crash.
- `set_if_absent` is atomic: of N concurrent callers, exactly one gets `True`.
- `compare_and_set` is atomic. It replaces the value only when the stored value equals `expected` byte for byte. `value=None` deletes. `ttl_s` sets a new TTL on a replace; `None` stores the new value with no expiry. It returns `False` when the key is absent or the value differs.
- Every method raises `StateUnavailable` when the store fails. No other exception escapes.
- `PortBundle` gains `state: StatePort = field(default_factory=InMemoryState)`, so bundles that tests build today still build.

**Fake:** `packages/chassis/src/chassis/fakes/state.py`, `InMemoryState(clock: Callable[[], float] = time.monotonic)`. A dict of `(value, expires_at)`. Expired entries are dropped on read. An `asyncio.Lock` is not needed (no await inside each method), but the suite proves atomicity anyway. `fail_next(exc)` scripts one `StateUnavailable`. `calls` records `(method, key)` for tests.

**Suite:** `packages/contract-suites/src/chassis_contracts/state.py`, `StatePortContract`. Fixtures: `state_port` (required), `key_prefix` (default: a fresh `uuid4().hex` per test, so runs against a shared Valkey never collide), `broken_state_port` (optional, skips by default). Cases:

1. `test_get_of_a_missing_key_is_none`
2. `test_set_then_get_round_trips_any_bytes` (includes `b"\x00\xff"`)
3. `test_set_overwrites`
4. `test_set_if_absent_claims_once_and_never_overwrites`
5. `test_concurrent_set_if_absent_has_one_winner` (20 tasks with `asyncio.gather`)
6. `test_a_value_expires_after_its_ttl` (`ttl_s=0.2`, read after 0.6 s)
7. `test_an_expired_claim_can_be_claimed_again`
8. `test_compare_and_set_replaces_only_on_a_match`
9. `test_compare_and_set_on_a_missing_key_is_false`
10. `test_compare_and_set_with_none_deletes_on_a_match`
11. `test_compare_and_set_renews_the_ttl`
12. `test_delete_of_a_missing_key_is_fine`
13. `test_a_store_failure_is_state_unavailable` (uses `broken_state_port`)

**Valkey adapter:** `packages/chassis/src/chassis/adapters/valkey/state.py`, `ValkeyState`.

- Library: `valkey` (valkey-py, the BSD-licensed fork of redis-py), `valkey.asyncio.Valkey`. suggested: pin exactly to the newest 6.x on the day, in `packages/chassis/pyproject.toml`, and write the version in the PR. Not `valkey-glide`: it ships a Rust core, a bigger image, and more memory per replica, and this PoC measures the replica's cost.
- `ValkeyState.from_env()` reads `VALKEY_URL` (required, for example `valkey://valkey:6379/0`; `valkeys://` for TLS), `VALKEY_USERNAME` and `VALKEY_PASSWORD` (optional, passed as arguments, never put in the URL, never logged). suggested: `socket_timeout=1.0`, `socket_connect_timeout=1.0`, `health_check_interval=10`. A missing `VALKEY_URL` raises `LookupError`; the factory turns it into `AdapterNotAvailable`, as the LiteLLM factory does.
- `set` is `SET key value PX ms` (ceil to ms, at least 1). `set_if_absent` is `SET key value NX PX ms`. `compare_and_set` is one Lua script registered with `register_script`: GET, compare, then SET with PX, SET with no TTL, or DEL.
- `valkey.exceptions.ConnectionError`, `TimeoutError`, and `ResponseError` become `StateUnavailable` with the class name only. `aclose` closes the pool.

**Names and defaults:** `spec.adapters.state`: `memory` or `valkey`. Profile defaults: `fake` → `memory`, `local` → `valkey`, `cloud` → `valkey`. `cloud` refuses `memory` (already the rule for every port in `IN_MEMORY`). `configs/local.yaml` and `configs/sidecar.yaml` add `state: memory`, so the PoC-1 to PoC-3 Compose stacks, which have no Valkey, keep working.

## 2. `EventPort`

**File:** `packages/chassis/src/chassis/ports/events.py`.

```python
class CloudEvent(BaseModel):
    """CloudEvents 1.0, structured mode, JSON. Extensions are top-level attributes."""
    model_config = ConfigDict(extra="forbid")
    specversion: Literal["1.0"] = "1.0"
    id: str                                   # uuid4().hex
    source: str                               # suggested: "/agents/<agent name>"
    type: str                                 # the topic name, e.g. "agents.task.completed.v1"
    subject: str | None = None                # suggested: the request_id
    time: datetime                            # UTC, RFC 3339
    datacontenttype: Literal["application/json"] = "application/json"
    dataschema: str | None = None             # suggested: "task-result.v1.json"
    data: dict[str, Any]
    traceparent: str | None = None            # extensions from 019 H-17
    idempotencykey: str | None = None
    configversion: str | None = None
    modelroute: str | None = None
    partitionkey: str | None = None           # CloudEvents partitioning extension

class PublishFailed(RuntimeError): ...        # after the adapter's own retries

EventHandler = Callable[[CloudEvent], Awaitable[None]]

class Subscription(Protocol):
    async def close(self) -> None: ...

class EventPort(Protocol):
    async def publish(self, topic: str, event: CloudEvent) -> None: ...
    async def subscribe(
        self, topic: str, handler: EventHandler, *, group: str, max_attempts: int = 3
    ) -> Subscription: ...
    async def aclose(self) -> None: ...
```

The promise, which the suite checks:

- **Publish** returns once the broker has the event (Kafka: `acks=all`; Dapr: a 204). A transient failure is retried by the adapter, suggested: 3 attempts with 0.1 s and 0.5 s back-off. Then it raises `PublishFailed`.
- **Delivery** is at least once. A handler that returns acknowledges the event. A handler that raises gets the event again, up to `max_attempts` deliveries in total. Then the event goes to the dead-letter topic `<topic>.dlq` (suggested) with two more extensions: `deadletterreason` (the exception class name only, never its message) and `deadletterattempts`. Consumers dedupe by `id`.
- **Order:** events with the same `partitionkey` reach one group in publish order. suggested: `partitionkey` is the run's `idempotency_key` (019 H-17 names `run_id`, which does not exist yet).
- **Groups:** each `group` gets every event once (as a Kafka consumer group). suggested: the group is the agent name.
- **Wire form:** the structured JSON CloudEvent (`model_dump_json(exclude_none=True)`), content type `application/cloudevents+json`, the same bytes for Kafka (record value; record key = `partitionkey`) and Dapr (passed through as is, not wrapped again).
- No `cloudevents` SDK. The model above is the whole envelope (about 30 lines). The suite validates every published event against the CloudEvents 1.0 JSON Schema, vendored as test data in `packages/contract-suites/src/chassis_contracts/data/cloudevents-1.0.schema.json`, with `jsonschema`, already a dev dependency.
- `PortBundle` gains `events: EventPort = field(default_factory=NoEvents)`. `NoEvents` (in `ports/events.py`, like `NoTools`) drops each publish and counts nothing; `subscribe` raises `RuntimeError("no events adapter")`.

**Fake:** `packages/chassis/src/chassis/fakes/events.py`, `InMemoryBus`. One `asyncio.Queue` per (topic, group), one delivery task per subscription, the same retry and dead-letter rules, `published: list[tuple[str, CloudEvent]]` for tests, and `fail_next_publish()`.

**Suite:** `packages/contract-suites/src/chassis_contracts/events.py`, `EventPortContract`. Fixtures: `event_port` (required), `topic` (default: `test.<uuid>.event.v1`, fresh per test), `deliver_timeout_s` (default 5; Kafka and Dapr bindings set 30). Cases:

1. `test_a_published_event_reaches_a_subscriber`
2. `test_every_published_event_is_a_valid_cloudevent_with_its_extensions`
3. `test_events_with_one_partition_key_keep_their_order` (20 events)
4. `test_a_failing_handler_gets_the_event_again`
5. `test_after_max_attempts_the_event_goes_to_the_dead_letter_topic` (subscribes to `<topic>.dlq`, checks `deadletterattempts` and that `deadletterreason` holds no message text)
6. `test_two_groups_each_get_every_event`
7. `test_a_closed_subscription_gets_nothing_more`
8. `test_publish_to_a_broken_broker_raises_publish_failed` (optional fixture `broken_event_port`, skips by default)

**Kafka adapter (the broker client in the chassis):** `packages/chassis/src/chassis/adapters/kafka/events.py`, `KafkaEvents`.

- Library: `aiokafka` (pure Python, asyncio, Apache-2.0, no librdkafka in the image). suggested: pin exactly to the newest release on the day. Not `confluent-kafka`: it adds a native library to a read-only image and its asyncio API is newer.
- `from_env()`: `KAFKA_BOOTSTRAP_SERVERS` (required), `KAFKA_SECURITY_PROTOCOL` (default `PLAINTEXT`). SASL arrives with 020 X-8, not here.
- Producer: `acks="all"`, `enable_idempotence=True`, `linger_ms=5` (suggested), started on the first publish. Consumer per subscription: `enable_auto_commit=False`, `auto_offset_reset="earliest"` (suggested), commit after the handler returns or after the dead-letter publish. Retries happen in place, with suggested back-off 0.2 s then 1 s.
- Topics are created by the broker (`auto.create.topics.enable=true` in the PoC broker only; suggested: 3 partitions). Production topic creation is 020 X-8.

**Dapr adapter:** `packages/chassis/src/chassis/adapters/dapr/events.py`, `DaprEvents`.

- No Dapr SDK. httpx to daprd on localhost: `POST {DAPR_HTTP_ENDPOINT}/v1.0/publish/{DAPR_PUBSUB_NAME}/{topic}?metadata.partitionKey=<key>` with `Content-Type: application/cloudevents+json` and the header `dapr-api-token: $DAPR_API_TOKEN`. `from_env()`: `DAPR_HTTP_ENDPOINT` (default `http://127.0.0.1:3500`), `DAPR_PUBSUB_NAME` (default `pubsub`), `DAPR_API_TOKEN` (required), `APP_API_TOKEN` (required: daprd sends it on every call to the app). `trust_env=False`, as the LiteLLM client.
- Subscribe is programmatic: the adapter keeps a route table and exposes `inbound_routes() -> list[starlette.routing.Route]`: `GET /dapr/subscribe` (the list of `{pubsubname, topic, route, deadLetterTopic}`) and `POST /dapr/events/{topic}`. The handler answers `{"status": "SUCCESS"}`, `{"status": "RETRY"}` on an exception, and `{"status": "DROP"}` for a body that is not a valid CloudEvent. A call without the right `dapr-api-token` header (the `APP_API_TOKEN`) is 401.
- These routes are mounted on the **proxy app** (localhost only), never on the public port, and only when `ports.events` has `inbound_routes` (`server/proxy_app.py`, same `getattr` pattern as `aclose`). daprd runs with `--app-port 8090`.
- Retries and the dead-letter topic come from Dapr: the subscription's `deadLetterTopic` and a resiliency policy with `maxRetries: max_attempts - 1` (`deploy/compose/dapr/resiliency.yaml`). `max_attempts` on `subscribe` is checked against that file at startup and a mismatch is logged, not enforced in code.
- Closing the API to the workload: `DAPR_API_TOKEN` and `APP_API_TOKEN` are set only on the chassis and daprd containers. Measured in PoC-4: a publish from the workload container without the token is 401.

**Names and defaults:** `spec.adapters.events`: `none`, `memory`, `kafka`, `dapr`. Profile defaults: `none` in all three profiles (suggested: events are opt-in per agent until DEC-1, and `fake` keeps building before the in-memory bus exists). A test or an agent that wants events names `memory`, `kafka`, or `dapr`. `none` is not an in-memory double, so `cloud` allows it; `cloud` refuses `memory`.

**What the chassis uses events for in PoC-4.** Two uses, the second optional:

1. **Result events** (019 H-17). `spec.events.result_events: true` (default `false`). After a run ends, the chassis publishes `agents.task.completed.v1` (an `end` event) or `agents.task.failed.v1` (an `error` event). The payload is `TaskResult`, a new pure model in `chassis/core/results.py`: `{agent, agent_version, request_id, idempotency_key, status, input, output, usage, versions}`, published as `schemas/task-result.v1.json`. Publishing runs in a background task, never changes the response, and is never awaited by the request. A `PublishFailed` is counted as `chassis.events.publish_failed{topic}` (suggested) and logged without the payload. A replay publishes nothing: the first run already did. Shutdown waits for pending publishes, up to 5 s (suggested).
2. **Event-triggered runs** (optional, package P9). `spec.events.consume: {topic: agents.task.requested.v1, group: <agent name>}` (suggested names). The event's `data` is a native `RunRequest` body. The chassis runs it in complete mode through `serve`, with the event's `idempotencykey` (or its `id` when unset) as the idempotency key, so a redelivered event replays instead of running twice. The result goes out as a result event. The `Interface` literal gains `event` for telemetry. If P9 does not fit the week, the suites alone exercise `subscribe`, and the note says so.

`spec.events` is restart-only.

## 3. `ConfigPort` over MinIO, and the config loader

**Adapter:** `packages/chassis/src/chassis/adapters/s3/config.py`, `S3Config`, registered as both `minio` and `s3` (one S3 API).

- Library: `minio` (the MinIO Python SDK, Apache-2.0, a generic S3 client; small: urllib3, certifi, argon2-cffi, pycryptodome). suggested: pin exactly to the newest 7.x on the day. It is synchronous, so every call runs in `asyncio.to_thread`. Not `aioboto3` or `aiobotocore`: botocore adds tens of MiB of resident memory to every replica, and this PoC measures that memory.
- `from_env()`: `CONFIG_S3_ENDPOINT` (host:port, required), `CONFIG_S3_SECURE` (default `true`; `false` in Compose), `CONFIG_S3_REGION` (optional), `CONFIG_S3_BUCKET` (suggested default `agent-configs`), `CONFIG_S3_PREFIX` (suggested default `agents/`), `CONFIG_S3_ACCESS_KEY` and `CONFIG_S3_SECRET_KEY` (required, never logged), `CONFIG_POLL_INTERVAL_S` (suggested default `5`; tests set `0.1`).
- `load(name)` reads `<prefix><name>.yaml`, parses YAML to a dict, and returns `LoadedConfig(name, version, data)`. `version` is the S3 `VersionId` when the bucket has versioning on, else the ETag without quotes. `NoSuchKey` is `ConfigNotFound`. Any other S3 error raises a new `ConfigUnavailable(RuntimeError)` from `ports/config.py`.
- `subscribe(name, callback)` starts one polling task on the running loop: every interval (suggested ±10% jitter, so replicas do not poll together) it calls `stat_object`. When the version changed, it calls `load` and awaits `callback`. A failed poll is logged and counted (`chassis.config.poll_failed`), never raised. The returned `Unsubscribe` cancels the task. `aclose()` cancels every task.
- Change to the suite (`chassis_contracts/config.py`): notification may be eventual. `test_change_gives_new_version_and_notifies` waits up to a fixture `notify_timeout_s` (default 2, the S3 binding sets 5) for the callback. The fake still notifies at once. Add `test_unsubscribe_stops_notifications`.

**Where the agent config lives.**

- The **bootstrap file** (`chassis serve --config`) stays what it is: a whole `ChassisConfig`. It names the profile, the agent, `spec.adapters`, and `spec.engine`, so the chassis knows how to reach the store.
- The **store document** is a whole `ChassisConfig` too, at `s3://agent-configs/agents/<agent.name>.yaml` (suggested). The bucket has versioning on.
- With `spec.adapters.config: memory` there is no store document. The bootstrap file is the config, and reload is driven by `InMemoryConfig.put` in tests. With any real config adapter, a missing document fails startup.

**Which schema.** `ChassisConfig` is the schema. `make schemas` writes it to `packages/chassis/schemas/chassis-config.v0.json` (`ChassisConfig.model_json_schema()`, the same drift test as the other schemas). The seed step uploads that file to `s3://agent-configs/schemas/chassis-config.v0.json`, where 010 H-12 puts it. The chassis validates with Pydantic, which the schema is generated from; `jsonschema` stays a dev dependency. A test proves that every bad fixture fails both the Pydantic model and the published schema. The C.1 governance fields, `risk_class`, and `vault://` checks stay open in 010 H-12; they are not PoC-4.

**The loader:** `packages/chassis/src/chassis/server/config_loader.py`, `ConfigReloader(app_state, port, bootstrap)`.

- `start()`, in the lifespan after the ports are built: load the store document, validate it, check the restart-only fields against the bootstrap, set `state.config`, then `subscribe`. An invalid document at start fails startup, and the error names the failing field (Pydantic's `loc`), never a value.
- `on_change(loaded)`: parse and validate. Then compare the restart-only fields with the active config. If all pass, swap `state.config` in one assignment, count `chassis.config.reloaded`, and log the old and new `version`. If not, keep the active config, count `chassis.config.rejected{reason}` (`invalid` or `restart_required`), and log the field paths.
- `version` of the active config is the document's own `version` field when set, else the content hash of the document bytes (as `load_config` does today). So every replica that reads the same bytes reports the same `versions.config`. The S3 version id only detects change.

**What reloads in PoC-4.** The pipeline already reads `state.config` per call, and a run keeps the `Context` it opened with. Reloadable:

| Field | Read where |
| ----- | ---------- |
| `version` | `versions_for`, per response |
| `spec.limits.*` | `enforce_limits`, per call; `BodyLimit` changes to read `public.state.config.spec.limits.body_bytes_max` per request |
| `spec.model.route` | `context_for`, per run |
| `spec.prompt.version` | `versions_for` |
| `spec.idempotency.ttl_s` | the idempotency layer, per run |

Restart-only, refused on reload with `restart_required`: `profile`, `agent` (name and version), `spec.adapters`, `spec.engine`, `spec.interfaces` (routes are mounted at start), `spec.events`, `spec.idempotency.enabled`, and `spec.idempotency.lease_s`. Known limit: the OpenAPI description of the limits (`describe_run`) is built once at start, so `/openapi.json` shows the start values. Recorded in contract v3; suggested: accept for PoC-4.

`/manifest` is built per request, so it shows the active `versions.config`.

## 4. Idempotency

**Config:** a new `spec.idempotency` block in `server/config.py` (010 H-12 and 018 H-18 name `harness.idempotency.ttl_s`; the chassis config has no `harness` block yet).

```yaml
spec:
  idempotency:
    enabled: true        # suggested
    ttl_s: 86400         # suggested: a cached result lives one day
    lease_s: 5           # suggested: a claim expires 5 s after its owner stops renewing it
    wait_poll_ms: 100    # suggested
    max_entry_bytes: 1048576   # suggested: a bigger result is not cached
```

**When it applies.** Only to a key the client sent: the `Idempotency-Key` header or the native body's `idempotency_key`. A minted key never repeats, so a call without one never touches `StatePort`. `ResolvedIds` gains `key_from: Literal["header", "body", "minted"]` (`server/interfaces/ids.py`). Every interface is covered: native, OpenAI, Anthropic, and MCP (its inner hop already forwards `Idempotency-Key`). Event-triggered runs use the event's key.

**The cache key.** `chassis:idem:v1:<agent.name>:<sha256(idempotency_key) hex>` (suggested). Scoped by agent, so two agents on one Valkey never collide. Hashed, so a long or odd key cannot shape the store key.

**The fingerprint.** `sha256` of the canonical JSON (`sort_keys`, no spaces) of `{agent, input, context_ref}`. Not part of it: the ids, `stream`, `budget`, `agent_version`, and the interface. So a retry during a rolling upgrade, a retry that switches to streaming, or the same call through another interface still matches.

**The entry** (JSON bytes):

- Claimed: `{"v": 1, "state": "running", "owner": "<replica id>:<uuid4>", "fingerprint": "..."}`. The replica id is the host name.
- Done: `{"v": 1, "state": "done", "fingerprint": "...", "events": [<wire events>], "response": <Response JSON>}`.

**The flow**, in `serve`, after `enforce_limits` and before `open_run`. A new module `server/idempotency.py` holds `Idempotency(state: StatePort, spec: IdempotencySpec, telemetry)` with one entry point:

```python
async def begin(self, request: Request, *, wait_ms: int) -> Claimed | Replay | Refusal
```

1. `set_if_absent(key, running_entry, ttl_s=lease_s)`. Won: return `Claimed`. `Claimed` starts a renew task: `compare_and_set(key, own_entry, own_entry, ttl_s=lease_s)` every `lease_s / 3`. If a renew returns `False`, the claim was lost: count `chassis.idempotency.lease_lost` and let the run finish without caching.
2. Lost: `get(key)`.
   - `done` with the same fingerprint: return `Replay(events, response)`.
   - Any entry with another fingerprint: return `Refusal("idempotency_conflict")`.
   - `running` with the same fingerprint: poll every `wait_poll_ms` until it is `done` (replay), gone (go to step 1 and try to claim, which is the takeover after a crash), or `wait_ms` passes. `wait_ms` is the caller's own `budget.timeout_ms`. Past it: `Refusal("idempotency_in_progress")`.
3. `StateUnavailable` at any step: `Refusal("state_unavailable")`. suggested: fail closed for a keyed call; a call with no key is not affected.

**Finish.** `Claimed.finish(run)`, always called once:

- The run ended with an `end` event (`ok`, `retry`, or `fallback`) and the entry fits `max_entry_bytes`: `compare_and_set(key, own_entry, done_entry, ttl_s=ttl_s)`. A miss counts `lease_lost`.
- Anything else (an `error` event, a run closed early by a client that left, an exception, a too-large result): `compare_and_set(key, own_entry, None)`, so the next retry runs again. suggested: errors are never cached; a retryable error must be retried, and a non-retryable one costs a rerun at most.
- A finish failure is counted and logged, never sent to the client.
- The complete path calls `finish` after `_complete`. The stream path calls it from `RunStream`'s `on_close`, after the events are closed. `pipeline.Run` gains two read-only properties for this: `seen: tuple[Event, ...]` and `done: bool`.

**Replay.** The stored `Response` is the original result, byte for byte: the original `request_id`, `trace_id`, and `versions`. Complete mode: `adapter.complete(response, meta)` with `meta.for_request` of the original request. Stream mode: `adapter.stream` over the stored events, so the client gets the same frames at once (the hold rule passes, since the stored run ended with `end`). No run is opened, no `RunRecord` is held, the engine and the model are not called, and nothing is charged. A `chassis.idempotent_replay` span (attributes `interface`, `mode`) and the counter `chassis.idempotency.replayed{interface}` mark it. A duplicate that waited and then replays is the same.

**The header.** Every replayed answer carries `Idempotent-Replayed: true` (suggested name, as Stripe and the IETF draft use), on native, OpenAI, and Anthropic, complete and stream. A first run carries no such header. MCP clients cannot see it (the inner hop's headers are not passed back); the envelope is the same anyway.

**Refusals** (new rows in the status table, suggested):

| Code | Native | OpenAI and Anthropic | `x-should-retry` |
| ---- | ------ | -------------------- | ---------------- |
| `idempotency_conflict` | 422 `{"detail": {code, message}}` | 422, `invalid_request_error` | `false` |
| `idempotency_in_progress` | 409 `{"detail": {code, message}}` | 409, `server_error` / `conflict_error` | `true` |
| `state_unavailable` | 503 `{"detail": str}` | 503, `server_error` / `overloaded_error` | `true` |

These go through `adapter.error`, with `status_for` extended in `core/inbound.py` and fixed texts added to `PUBLIC_MESSAGES`.

**Where it sits, with the in-memory store.** When there is no Valkey, `spec.adapters.state: memory` gives one `InMemoryState` per process. That is correct for one replica and for tests. Two replicas in tests share one `InMemoryState` object to stand for Valkey. The `RunPipeline` builds `Idempotency` lazily from `state.ports.state` and `state.config.spec.idempotency`, so `app.py` does not change for it.

## 5. Timeout and budget per call

**What exists** (PoC-2 and PoC-3, with tests):

- `budget.timeout_ms` (default 30 000) is the A2A connector's per-read timeout and whole-run deadline in every lane. Past it, the run ends with `a2a.timeout` (retryable, 504 on the chat formats) and the A2A task is cancelled (`packages/chassis/tests/test_a2a_sidecar.py::test_timeout_mid_run_yields_a2a_timeout_and_cancels`). In `inprocess` the deadline ends the stream but not `handle`.
- `budget.max_tokens` (default 2000) is the run's whole budget in the model proxy: reserved per call, settled on usage, and 429 `budget_exhausted` when nothing is left (`test_model_proxy.py`, `pocs/poc-02-two-engines-one-contract/tests/test_run_correlation.py`).
- `spec.limits` refuses a budget above a ceiling on every interface (`test_limits.py`).
- The workloads map a model timeout to a retryable error (`echo-python`, `echo-pydanticai`, `echo-langgraph` tests).

**The gap PoC-4 closes:**

1. **A complete call whose client hangs up keeps running** (PoC-3 debt, owner PoC-4). `serve` gains `disconnected: Callable[[], Awaitable[bool]] | None`. The native, OpenAI, and Anthropic routes pass `http.is_disconnected`. In complete mode, a watcher task polls it every 250 ms (suggested). On a disconnect it closes the run's events, which cancels the A2A task, frees the run's budget in the model proxy, and releases the idempotency claim. Counted as `chassis.client_disconnected{interface}` (suggested). MCP stays bounded by `budget.timeout_ms` only: its inner ASGI hop does not see the MCP client leave. Recorded in contract v3.
2. **The wait for a duplicate is bounded by the caller's own `timeout_ms`** (section 4).
3. **Every engine is proven, not only `echo-python`.** One scenario test per engine in the `sidecar` lane: past `timeout_ms` the answer is `a2a.timeout` and the workload's task is cancelled; past `max_tokens` the next model call is refused with `budget_exhausted`. These pin existing behavior across the four engines.
4. **Not in PoC-4:** agent-level budget defaults (`harness.timeout_ms`, `harness.budget.max_tokens`, "the smaller wins") and retry and fallback. They stay in 017 H-4 for PoC-8.

## 6. Graceful shutdown with two containers

**Chassis side** (`server/cli.py`, a new `server/lifecycle.py`).

- Today `chassis serve` runs two `uvicorn.Server`s with `asyncio.gather`. Each `serve()` installs its own signal handlers (`capture_signals` in uvicorn 0.54), the second one wins, and on exit uvicorn raises the captured signal again. So PoC-4 turns uvicorn's signal handling off: a subclass `QuietServer(uvicorn.Server)` overrides `capture_signals` with a context manager that does nothing. The chassis installs its own handlers with `loop.add_signal_handler` for SIGTERM and SIGINT.
- New flags: `--drain-delay-s` (suggested default 5: the time a load balancer or Kubernetes endpoints take to stop sending) and `--drain-timeout-s` (suggested default 30: the default `budget.timeout_ms`). Compose uses `--drain-delay-s 3`.
- The order on the first SIGTERM:
  1. `state.draining = True`. `/ready` answers 503 with `reason: draining` at once. New calls are still served.
  2. Stop the event consumer, if any (it stops fetching; its in-flight runs go on).
  3. Sleep `drain_delay_s`.
  4. `public.should_exit = True`. uvicorn closes the public listener and waits for in-flight requests, streams included, for up to `drain_timeout_s` (`uvicorn.Config(timeout_graceful_shutdown=drain_timeout_s)`). Then it cancels what is left: those runs close, their A2A tasks are cancelled, and their idempotency claims are released, so a client retry runs elsewhere.
  5. The public lifespan's shutdown runs: wait for pending result-event publishes (up to 5 s), close the ports.
  6. Only now `proxy.should_exit = True`. The proxy listener stays up through steps 1 to 5, because in-flight workloads still call the model proxy and the tool endpoint.
  7. Exit 0.
- A second SIGTERM or SIGINT sets `force_exit` on both servers and exits at once.
- The existing rule stays: if either server stops on its own (a failed startup), the other stops too.

**Workload side.**

- `workload_a2a` (`packages/workload-a2a/src/workload_a2a/cli.py`, `build_server`): a new flag `--drain-timeout-s` (suggested default 30) sets `timeout_graceful_shutdown`. uvicorn's own SIGTERM handling is right here (one server per process): it stops accepting, closes idle keep-alive connections, and waits for in-flight `handle` streams. `chassis/adapters/a2a/server.py` is not touched; `build_server` exists only on the workload side, so the mirror rule (ADR-002) holds.
- `echo-typescript` (`src/main.ts`): today `server.close()` waits forever when the chassis holds a keep-alive connection. Change: on SIGTERM call `server.close(...)`, then `server.closeIdleConnections()`, and arm `setTimeout(() => process.exit(1), DRAIN_TIMEOUT_MS).unref()` (env `DRAIN_TIMEOUT_MS`, suggested default 30000). Exit 0 when the last connection ends.

**The order between the two containers.**

- Kubernetes, native sidecar (suggested winner): the workload is an init container with `restartPolicy: Always`, the chassis the main container. On delete, the chassis gets SIGTERM first and drains. The workload gets SIGTERM only after the chassis exits, with nothing in flight. `terminationGracePeriodSeconds: 45` (suggested: delay 5 + drain 30 + 10).
- Kubernetes, preStop: two plain containers. The chassis has `preStop: sleep 5` and `--drain-delay-s 0`. The workload has `preStop: sleep 35` (the chassis's preStop plus its drain timeout), so it gets SIGTERM after the chassis is done. `terminationGracePeriodSeconds: 50`. Both use the built-in `sleep` action (no `sleep` binary needed in a read-only image).
- Compose: `docker compose stop` stops a dependent first, which is the workload. That is the wrong order, so it is not the drill. The drill script sends SIGTERM to the chassis (`docker kill -s TERM`), waits for it to exit, then stops the workload. `stop_grace_period: 45s` on both.

## 7. Liveness and readiness

- **`GET /health`:** 200 while the process serves HTTP, draining included. Never looks at the workload, so a hung workload never restarts the chassis. The chassis's liveness probe.
- **`GET /ready`:** 200 only when all hold: the lifespan finished, the first config is loaded, not draining, and the workload probe is healthy. Else 503. The body gains an optional `reason`: `starting`, `draining`, or `workload_unreachable` (an additive OpenAPI change). The chassis's readiness probe and Traefik's health check.
- **The workload probe.** `EngineConnector` gains `async def probe(self) -> bool` (an additive port change; every implementer changes in the same package). `SidecarConnector`: GET `/.well-known/agent-card.json` over its own client with a 1 s timeout (suggested); `True` on 200. `InProcessConnector` and `FakeEngine`: `True`. A `ReadinessMonitor` task (`server/readiness.py`, started in the lifespan) probes every 2 s (suggested). After 3 failures in a row (suggested) the workload is unhealthy; one success makes it healthy again. `/ready` reads the cached result and never calls the workload itself. `ProbeSettings(interval_s=2.0, timeout_s=1.0, failures=3)` is a constructor argument of `create_app`, so tests run fast. No CLI flag (suggested).
- **The workload's own liveness probe** (Kubernetes). The workload listens on 127.0.0.1 only, and the kubelet's `httpGet` goes to the pod IP, so it is an `exec` probe: Python workloads run `python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2).status == 200 else 1)"`; the TypeScript workload runs the same with `node -e "fetch(...)"`. suggested: `periodSeconds: 10`, `timeoutSeconds: 3`, `failureThreshold: 3`. The exec costs one interpreter start per probe; the load test records it. A liveness failure restarts the workload container; the pod and its network stay. That meets the exit criterion's intent ("a hung workload restarts"), and the README says so.
- **How a hang is simulated.** Compose: `docker pause poc04-workload-1-1`. kind: from the node, `kill -STOP` the workload's host PID (`docker exec poc04-control-plane sh -c 'kill -STOP $(crictl inspect --output go-template --template "{{.info.pid}}" <id>)'`). Sending SIGSTOP from inside the pod does not work: a namespace's PID 1 ignores it. No hang switch goes into any workload's code.

## 8. Deploy

### 8a. Compose: `deploy/compose/docker-compose.scale.yaml`

A file of its own, not an overlay on `docker-compose.yaml`: `name: poc04`, one network `poc04` (`networks.default.name: poc04`). It never names, stops, or reuses a container outside the project. Today this machine runs other stacks (`paligo-*`, `opensearch-node1`) in a Docker VM with 14 CPUs and about 7.7 GiB of memory. So every scripted command passes `-p poc04` and `-f docker-compose.scale.yaml`, never `docker compose down` without them, and never `docker system prune`. suggested: the whole stack stays under 3 GiB (limits below).

Services:

| Service | Image | Notes |
| ------- | ----- | ----- |
| `fake-model-server` | `agent-platform/fake-model-server:poc04` (built) | The model. The chassis calls it directly: `LITELLM_BASE_URL=http://fake-model-server:8081/v1`. No LiteLLM in the measured path, so the numbers are about the chassis. |
| `valkey` | `valkey/valkey:8-alpine@sha256:...` | `--save "" --appendonly no --maxmemory 256mb --requirepass $VALKEY_PASSWORD`. No published port. |
| `minio` | `minio/minio:<tag>@sha256:...` | Root user from the generated env. No published port. |
| `minio-init` | `minio/mc:<tag>@sha256:...` | One shot: make the bucket `agent-configs`, turn versioning on, upload `agents/echo.yaml` and `schemas/chassis-config.v0.json`, create a read-only user for the chassis (policy on that bucket only). |
| `kafka` | `apache/kafka:<4.x tag>@sha256:...` | Profile `events`. KRaft, one node, `KAFKA_HEAP_OPTS=-Xmx512m`, auto-create topics, 3 partitions. No published port. |
| `traefik` | `traefik:v3.<x>@sha256:...` | File provider (`deploy/compose/traefik/poc04.yaml`), no Docker socket. Publishes `127.0.0.1:18080:80` only (suggested; 8080 is the PoC-1 stack's). Health check on `/ready` every 1 s, timeout 1 s. No retry middleware, so the client's own retry is what the drill tests. |
| `chassis-1` to `chassis-4` | `agent-platform/chassis:poc04` | From the anchor `x-chassis`. |
| `workload-1` to `workload-4` | `${WORKLOAD_IMAGE:-agent-platform/echo-python:poc04}` | From the anchor `x-workload`, `network_mode: service:chassis-N`. |

**Pairs.** Compose cannot scale two services as one unit when one joins the other's network namespace, so the pairs are explicit. `chassis-1` and `workload-1` have no profile and always start. Pair 2 has `profiles: [pairs2, pairs4]`. Pairs 3 and 4 have `profiles: [pairs4]`. So `--profile pairs2` gives 2 pairs and `--profile pairs4` gives 4. The anchors hold everything else; each pair differs only in its name, its `network_mode`, and its `depends_on`. Traefik lists all four servers; a pair that is not running fails the health check and gets no traffic.

**The chassis service** (`x-chassis`): `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]`, `user: "10001:10001"` (the image's `chassis` user), `cpus: 1.0` and `mem_limit: 512m` (suggested: a ceiling for the measurement, not a reservation), `stop_grace_period: 45s`. Config: `packages/chassis/configs/scale.yaml` mounted read-only (profile `local`, `connector: sidecar`, `url: http://127.0.0.1:9000`, adapters `model: litellm`, `config: minio`, `state: valkey`, `telemetry: memory`, `tools: fake`, `events: none`). Environment: `LITELLM_BASE_URL`, `VALKEY_URL`, `VALKEY_PASSWORD`, the `CONFIG_S3_*` set, and `KAFKA_BOOTSTRAP_SERVERS` in the events profile. The entrypoint is the PoC-2 sidecar one (wait for the agent card, bind the container address), plus `--drain-delay-s 3 --drain-timeout-s 30`.

**The workload service** (`x-workload`): the PoC-2 hardening anchor as is (`read_only`, `tmpfs: [/tmp]`, `cap_drop`, `no-new-privileges`, `user: 10001:10001`), the four allowed variables only, `cpus: 1.0`, `mem_limit: 512m`, `stop_grace_period: 45s`, and `DRAIN_TIMEOUT_MS=30000` for the TypeScript image. No health check in this file: the chassis's `/ready` covers the workload (section 7).

**Secrets.** No value in any file. `deploy/compose/scale.sh` (the one entry point) generates `VALKEY_PASSWORD`, `MINIO_ROOT_PASSWORD`, the chassis's MinIO key, `DAPR_API_TOKEN`, and `APP_API_TOKEN` with `openssl rand -hex 24` into the shell environment of that run, and the compose file requires them with `${VAR:?set by scale.sh}`. Only the chassis (and daprd in the Dapr variant) gets them. Known gap, recorded in `SECURITY.md` section 7: the PoC Kafka listener is PLAINTEXT with no auth, so a workload sharing the network could publish directly; broker auth is 020 X-8.

**The Dapr variant:** `deploy/compose/docker-compose.scale-dapr.yaml`, an overlay used with `--profile events`. It adds `daprd-1` to `daprd-4` (`daprio/daprd:1.<x>@sha256:...`), each with `network_mode: service:chassis-N`, the same pair profiles, `--app-id echo --app-port 8090 --dapr-http-port 3500 --resources-path /components`, `DAPR_API_TOKEN` and `APP_API_TOKEN`, and the components in `deploy/compose/dapr/` (`pubsub.yaml`: `pubsub.kafka` on `kafka:9092`; `resiliency.yaml`: 2 retries; `subscription` is programmatic). It sets the chassis's `spec.adapters.events: dapr` through a second config file, `configs/scale-dapr.yaml`.

**Images.** `scale.sh build` builds the chassis, the fake model server, and the four workload images with the tag `poc04`. `WORKLOAD_IMAGE` picks the engine for one run.

### 8b. kind: `deploy/kind/`

kind is at `/opt/homebrew/bin/kind` already. Plain manifests with kustomize (`kubectl apply -k`), no Helm.

```
deploy/kind/
  cluster.yaml                   # name poc04; kindest/node v1.33.x pinned by digest; host 127.0.0.1:18081 -> nodePort 30080
  poc04/
    base/                        # namespace poc04, fake-model-server, valkey, the agent Service (NodePort 30080, chassis port only), the bootstrap ConfigMap
    native-sidecar/              # Deployment: workload in initContainers with restartPolicy: Always; chassis the main container
    prestop/                     # Deployment: two plain containers with preStop sleeps
  run.sh                         # create the cluster, build and `kind load` images, generate the secret with kubectl, apply a variant, run a drill
```

The Deployment, both variants: `replicas: 3`, `strategy: RollingUpdate` with `maxUnavailable: 0` and `maxSurge: 1`, `automountServiceAccountToken: false`, both containers with `readOnlyRootFilesystem: true`, `runAsNonRoot: true`, `runAsUser: 10001`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, and an `emptyDir` at `/tmp`. Chassis: readiness `httpGet /ready` every 2 s, liveness `httpGet /health` every 10 s, requests 100m CPU and 128Mi (ADR-001's upper estimate), no CPU limit (suggested). Workload: the exec liveness probe from section 7, and a `startupProbe` with the same command. The ConfigMap holds the bootstrap with `config: memory` and `state: valkey`; MinIO is not needed on kind for these drills.

Drills, each run for both variants and both `echo-python` and `echo-typescript`:

1. **Rolling restart under load.** `kubectl rollout restart deployment/agent` while `pocs/poc-04-stateless-scalable/load/steady_client.py` sends 20 concurrent complete calls through `127.0.0.1:18081` with no retries. Pass: 0 failed requests.
2. **Hung workload.** SIGSTOP the workload in one pod (section 7). Pass: that pod's `/ready` turns false within 10 s, the workload container restarts within 60 s, and `/ready` is true again.

The variant with 0 failures in drill 1 on both engines wins and goes to 024 CH-3. If both pass, the native sidecar wins, because it needs no timing guess (suggested).

## 9. Load test

**Tool: Locust.** k6 is not installed, and Locust is Python, which the repo already runs. It is not a workspace dependency: `uv run --with "locust==<newest 2.x>" locust ...`, pinned in the script. `FastHttpUser`, `--processes 4`, headless, `--csv`.

**Files** (code for this iteration only, so in the PoC folder):

- `pocs/poc-04-stateless-scalable/load/locustfile.py`: one user class, `POST /v1/run`, complete mode, a fixed short input, a fresh `Idempotency-Key` per call (the realistic path, with Valkey in it). An env switch `LOAD_NO_KEY=1` sends no key, for the baseline.
- `pocs/poc-04-stateless-scalable/load/run_matrix.py`: for each engine in `echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-typescript`, and each pair count 1, 2, 4: bring the pairs up (`scale.sh up <engine> <pairs>`), wait for every `/ready`, warm up 10 s, run Locust for 60 s with 64 users (suggested), and sample `docker stats --no-stream --format '{{json .}}'` for the `poc04-*` containers every 2 s in parallel. Writes `notes/load/raw/<engine>-<pairs>p-*.csv` and appends to `notes/load/results.json`.
- `pocs/poc-04-stateless-scalable/load/steady_client.py`: the no-retry client for the kind and Compose drills; prints sent, ok, failed, and the failures.
- `pocs/poc-04-stateless-scalable/load/kill_drill.py`: the killed-replica drill (section 10).

**What is recorded** in `notes/2026-10-xx-load-results.md` (the date of the run), one table per engine:

- Throughput (requests per second) at 1, 2, and 4 pairs, errors, p50, and p95.
- Per replica, chassis and workload separately: CPU (mean and max, in vCPU) and memory (mean and max, in MiB), plus idle CPU and memory before the run, and chassis CPU per 100 requests per second.
- **The sidecar hop.** For `echo-python` at 1 pair and a fixed 8 users: the same run in the `sidecar` lane and in the `inprocess` lane (the chassis image already holds `echo_python`; `configs/scale-inprocess.yaml`). The hop is the difference in p50 and p95. It includes the model proxy's hop too, which is the real cost of the lane.
- The cost of idempotency: `echo-python`, 1 pair, with and without `LOAD_NO_KEY`.
- The host: CPU count, Docker VM memory, other stacks running.
- Next to ADR-001's figures: 0.05–0.1 vCPU, 128–256 MiB, a 1–3 ms hop. If any figure is far above (suggested: more than twice), the note says so and the Revisit rule goes to the epic owner.
- If the fake model server or Traefik saturates first (its CPU near 1.0 in the samples), the note says so: then the numbers bound the chassis from below.

`notes/load/results.json` is the evidence a gate test reads (section 10, criterion 4).

## 10. Tests

**The gate** (`make test`, offline, sockets off, keys stripped, Unix sockets allowed) runs everything with the fakes. Two replicas are two chassis apps on Unix sockets that share one `InMemoryState` and one `InMemoryBus`. A shared harness, `pocs/poc-04-stateless-scalable/tests/poc04_harness.py`, builds them on top of `poc03_harness` (`chassis_on_unix_sockets`, the workloads on Unix sockets, the outbound router).

**Integration** (`make test-integration`, new): `pytest -m network` on `packages/chassis/tests/integration` and `pocs/poc-04-stateless-scalable/tests`, sockets on, keys still stripped, Docker needed. Real adapters run against testcontainers: `testcontainers` 4.x (dev group; the `minio` and `kafka` extras), Valkey as a generic container. The helpers live in `packages/contract-suites/src/chassis_contracts/containers/` (`valkey.py`, `minio.py`, `kafka.py`, `dapr.py`). The daprd container calls back to the test's app over `host.docker.internal`; if that does not work on this Docker, the Dapr binding runs inside the Compose Dapr variant instead and the note says so.

**Drills** that need the Compose stack or kind are `network` tests too, and their output also goes into `notes/` as evidence: `make test-integration` does not start them unless `POC04_STACK=1` (Compose) or `POC04_KIND=1` (kind) is set.

One scenario test per exit criterion, in `pocs/poc-04-stateless-scalable/tests/`:

| # | Exit criterion | Offline test (gate) | `network` test and evidence |
| - | -------------- | ------------------- | --------------------------- |
| 1 | Swap drill | `test_swap_drill.py::test_state_port_swaps_by_config_only[memory]`, `::test_event_port_swaps_by_config_only[memory]` | the same tests with `[valkey]`, `[kafka]`, `[dapr]`; the suite bindings in `packages/chassis/tests/integration/` |
| 2 | Same key, any replica | `test_idempotency.py::test_a_repeated_key_returns_the_same_result_on_any_replica`, `::test_a_repeated_key_makes_no_second_model_call`, `::test_same_key_other_input_is_a_conflict`, `::test_concurrent_duplicates_run_once`, `::test_a_cached_result_replays_as_a_stream`, `::test_every_interface_replays_the_same_result`, `::test_an_error_is_not_cached`, `::test_the_cache_expires_after_ttl` | `test_idempotency_valkey.py::test_two_replicas_share_one_result_through_valkey` |
| 3 | Killed replica, client retries | `test_killed_replica.py::test_a_retry_after_a_dead_owner_runs_once_after_the_lease` (replica A's claim is left unrenewed, as after SIGKILL; B's retry waits for the lease and runs once) | `test_compose_scale.py::test_killing_a_pair_under_load_loses_no_retried_request` (`kill_drill.py`: steady load with retries on the same key, `docker kill` of pair 2; every call ends 200, and each key has one model call in the fake server's log) |
| 4 | Throughput grows | `test_load_results.py::test_throughput_grows_with_pairs_for_every_engine` (reads `notes/load/results.json`; RPS at 4 pairs > 2 pairs > 1 pair per engine, else the engine is flagged in the note) | the matrix run |
| 5 | Read-only root, both containers | `test_read_only_compose.py::test_compose_sets_read_only_on_every_chassis_and_workload`, `test_read_only_kind.py::test_kind_sets_read_only_on_both_containers` (parse the YAML) | `test_compose_scale.py::test_every_engine_answers_with_read_only_roots` (one call per engine, then `docker diff` shows no write outside `/tmp`) |
| 6 | Stopping a pair fails nothing in flight | `test_graceful_shutdown.py::test_the_chassis_drains_in_flight_runs_and_ready_goes_false_first`, `::test_the_proxy_listener_outlives_the_public_one`; `test_workload_drain.py::test_workload_a2a_finishes_in_flight_handle_on_sigterm`, `::test_typescript_workload_finishes_in_flight_handle_on_sigterm` | `test_compose_scale.py::test_stopping_a_pair_under_load_fails_no_request` (chassis SIGTERM first, then the workload) |
| 7 | Bad config rejected, last good kept | `test_config_reload.py::test_a_good_change_applies_without_restart`, `::test_a_bad_config_is_rejected_and_the_last_good_one_stays`, `::test_a_restart_only_change_is_refused`, `::test_bad_fixtures_fail_the_published_schema_too` | `test_config_minio.py::test_a_minio_change_takes_effect_without_restart` |
| 8 | Hidden state listed | `test_hidden_state.py::test_shuffled_calls_match_on_fresh_and_reused_workloads[<engine>]` (the 057 H-19 kept-data check: one reused workload against a fresh one per call, shuffled order, fake model) | `notes/2026-10-xx-hidden-state.md`: per engine, what keeps state (known candidates: the `global list_failures` counters in `echo_python/tools.py` and `echo_langgraph/tools.py`, the A2A server's in-memory task store, any LangGraph checkpointer), and a way out or a note to reject |
| 9 | Hung workload restarts; roles survive a rolling restart | `test_liveness.py::test_ready_goes_false_when_the_workload_hangs_and_health_stays_ok`, `::test_ready_comes_back_when_the_workload_answers` | `test_kind.py::test_a_hung_workload_is_restarted[native-sidecar]`, `::test_rolling_restart_fails_no_request[native-sidecar-python, native-sidecar-typescript, prestop-python, prestop-typescript]` |
| 10 | Dapr decision written | `test_records.py::test_the_dapr_decision_is_recorded` (ADR-004 exists and is linked from 001 DEC-1 and 019 H-17) | `notes/2026-10-xx-dapr-vs-broker.md` |
| 11 | Sidecar cost next to ADR-001 | `test_load_results.py::test_sidecar_cost_is_recorded_for_every_engine` (results hold hop p50 and p95, CPU, and memory) | the matrix run and the note |

Also, outside the PoC folder: `test_timeout_budget_per_engine.py` in `pocs/poc-04-stateless-scalable/tests/` (section 5, item 3: `a2a.timeout` with cancel, and `budget_exhausted`, for each of the four engines in the `sidecar` lane) and `packages/chassis/tests/test_client_disconnect.py` (section 5, item 1).

## 11. Contract impact

| Contract | Change | Version |
| -------- | ------ | ------- |
| Event schema (`events.v0.json`) | None | `schema_version` stays `"0"` |
| Envelope (`Request`, `Response`, `Context`) | None. `idempotency_key` is already on `Context` and reaches the workload | — |
| `handle` | None | — |
| A2A mapping | None | — |
| Ports | New `StatePort` and `EventPort`. `EngineConnector.probe()` (additive; all implementers change together). `ConfigPort` unchanged in shape; its suite allows eventual notification and gains `ConfigUnavailable` | contract v3 |
| `spec.*` | `spec.adapters.state`, `spec.adapters.events`, `spec.idempotency`, `spec.events`; the reload rule (section 3) | contract v3 |
| Public interfaces | Response header `Idempotent-Replayed: true`; codes `idempotency_conflict` (422), `idempotency_in_progress` (409), `state_unavailable` (503); `/ready` gains `reason` and its new meaning; a complete call ends when the client leaves | contract v3, additive |
| Outbound events (new) | CloudEvents 1.0 structured JSON on `agents.task.completed.v1` and `agents.task.failed.v1`, payload `task-result.v1.json` | the topic's major, `v1` |

So PoC-4 writes `docs/contracts/contract-v3.md`: a new version of the document, as v1 and v2 were, with every change additive over v2. No wire version moves. The config version and the replay marker do not enter the envelope: `versions.config` is already there, and the replay is a header, so a client that ignores it sees the same body.

**ADRs.**

- **ADR-004, Dapr or a broker client behind `EventPort`:** needed, written at the end of PoC-4 from the measurements (daprd's CPU and memory per replica, the work to close its API, lines of code for CloudEvents, retries, and the dead-letter topic on each side), by `chassis-architect` with the `adr` skill. Not written now.
- **Idempotency:** no ADR. 018 H-18 decides the behavior; the suggested values (lease, fail closed, errors not cached, the fingerprint fields) go into contract v3 and the issue's status. If the user disagrees with fail closed or with not caching errors, that becomes an ADR.
- **Container roles:** no ADR if the native sidecar wins, as 024 CH-3 already suggests; the result goes into CH-3 and the notes. If the preStop variant wins, write ADR-005, because CH-3 and gap (g) change.

## 12. Work breakdown

Rules for every package: read the folder's `CLAUDE.md` first; touch only the files listed as yours; create new files rather than edit a shared one; `make quick` before handing back; paste the command and its output. A file not listed here belongs to nobody in PoC-4: ask the orchestrator before touching it. No package commits. `pyproject.toml` files, `uv.lock`, and the `Makefile` are P0's only; a later package that needs a dependency asks the orchestrator.

Paths are short: `chassis/` is `packages/chassis/src/chassis/`, `ctests/` is `packages/chassis/tests/`, `suites/` is `packages/contract-suites/src/chassis_contracts/`, `poc/` is `pocs/poc-04-stateless-scalable/`.

### Wave 0: the seams (one package, alone)

**P0 Seams** (`developer`). Creates: `chassis/ports/state.py`, `chassis/ports/events.py` (with `CloudEvent`, `NoEvents`, `PublishFailed`), `chassis/fakes/state.py`, `chassis/core/results.py` (`TaskResult`), `suites/containers/__init__.py`, `ctests/integration/__init__.py` and `ctests/integration/conftest.py` (every test there is `network`; skip when Docker is not reachable), `poc/tests/conftest.py` (puts the PoC-2 and PoC-3 test folders on `sys.path`; skips Compose drills unless `POC04_STACK=1` and kind drills unless `POC04_KIND=1`), `scripts/check_integration.sh` (pytest `-m network`, sockets on, keys stripped). Edits: `chassis/ports/__init__.py`, `chassis/ports/bundle.py` (`state`, `events`), `chassis/ports/config.py` (`ConfigUnavailable`), `chassis/fakes/__init__.py` (`InMemoryState`), `chassis/profiles.py` (the whole PoC-4 part: `AdapterSpec.state` and `.events`, `PORTS`, `PROFILE_DEFAULTS`, and every new `REGISTRY` entry as a lazy factory that imports its adapter inside the function and turns a `ModuleNotFoundError` into `AdapterNotAvailable("... arrives in PoC-4")`, so no later package edits this file; `build_ports` builds `state` and `events`), `chassis/server/config.py` (`IdempotencySpec`, `EventsSpec`, `Spec.idempotency`, `Spec.events`, and the constants `RESTART_ONLY` and `RELOADABLE` as tuples of field paths), `packages/chassis/configs/local.yaml` and `sidecar.yaml` (`state: memory`), `ctests/test_profiles.py` and `ctests/test_server.py` (only the cases the new fields need), `packages/chassis/pyproject.toml` (`valkey`, `aiokafka`, `minio`, pinned exactly), root `pyproject.toml` (dev: `testcontainers[minio,kafka]` pinned; import-linter: `valkey`, `aiokafka`, and `minio` only under `chassis.adapters`, and none of them in `core` or `ports`), `uv.lock`, `Makefile` (`test-integration`, `load-test`, `kind-poc04`, `.PHONY`, `help` text). Done when `make check` is green, `build_ports("fake")` gives a bundle with `InMemoryState` and `NoEvents`, and naming `events: memory` or `state: valkey` before their package lands raises `AdapterNotAvailable` with the PoC-4 message.

### Wave 1: nine packages in parallel

| Package | Agent | Creates | Edits |
| ------- | ----- | ------- | ----- |
| **P1 State** | `developer` | `suites/state.py`, `ctests/test_state_contract.py` (binds `InMemoryState`), `chassis/adapters/valkey/__init__.py`, `chassis/adapters/valkey/state.py`, `suites/containers/valkey.py`, `ctests/integration/test_valkey_state_contract.py` | — |
| **P2 Config store** | `developer` | `chassis/adapters/s3/__init__.py`, `chassis/adapters/s3/config.py`, `suites/containers/minio.py`, `ctests/integration/test_s3_config_contract.py` | `suites/config.py` (eventual notify, unsubscribe case) |
| **P3 Events and Kafka** | `developer` | `chassis/fakes/events.py`, `suites/events.py`, `suites/data/cloudevents-1.0.schema.json`, `ctests/test_events_contract.py`, `chassis/adapters/kafka/__init__.py`, `chassis/adapters/kafka/events.py`, `suites/containers/kafka.py`, `ctests/integration/test_kafka_events_contract.py` | `chassis/fakes/__init__.py` (`InMemoryBus`) |
| **P4 Liveness and chassis shutdown** | `developer` | `chassis/server/readiness.py`, `chassis/server/lifecycle.py`, `ctests/test_readiness.py`, `ctests/test_shutdown.py`, `poc/tests/test_liveness.py`, `poc/tests/test_graceful_shutdown.py` | `chassis/ports/engine.py` (`probe`), `chassis/adapters/a2a/sidecar.py`, `chassis/adapters/a2a/inprocess.py`, `chassis/fakes/engine.py`, `suites/engine.py` (a probe case), `chassis/server/cli.py`, `chassis/server/app.py` (first of three: `/ready` with `reason`, `state.draining`, the monitor in the lifespan, `create_app(..., probe=)`) |
| **P5 Workload drain** | `developer` | `poc/tests/test_workload_drain.py` | `packages/workload-a2a/src/workload_a2a/cli.py`, `packages/workload-a2a/src/workload_a2a/server.py` (`build_server` only), the workload-a2a tests, `packages/workloads/echo-typescript/src/main.ts` and its test |
| **P6 Idempotency core** | `developer` | `chassis/server/idempotency.py`, `ctests/test_idempotency.py` (unit, over `InMemoryState` with a fake clock) | `chassis/core/inbound.py` (the three codes in `status_for`, `PUBLIC_MESSAGES`), `ctests/test_inbound.py` (their rows) |
| **P7 Compose files** | `developer`, then `platform-security` review | `deploy/compose/docker-compose.scale.yaml`, `deploy/compose/docker-compose.scale-dapr.yaml`, `deploy/compose/traefik/poc04.yaml`, `deploy/compose/dapr/pubsub.yaml`, `deploy/compose/dapr/resiliency.yaml`, `deploy/compose/scale.sh`, `packages/chassis/configs/scale.yaml`, `scale-dapr.yaml`, `scale-inprocess.yaml`, `poc/tests/test_read_only_compose.py` | `deploy/compose/SECURITY.md` (new section 7), `deploy/compose/README.md`, `packages/chassis/Dockerfile` (only if the read-only root needs it, e.g. `PYTHONDONTWRITEBYTECODE=1`). Checked with `docker compose -p poc04 -f ... config`; the stack is not run yet |
| **P8 kind files** | `developer`, then `platform-security` review | everything under `deploy/kind/` (section 8b), `poc/tests/test_read_only_kind.py` | `deploy/README.md` (a kind section) |
| **P9 Timeout and budget per engine** | `tester` | `poc/tests/test_timeout_budget_per_engine.py` | — |

P4 is the largest. If it runs long, split `server/cli.py` and `lifecycle.py` (shutdown) from `readiness.py` and the probe (liveness), in that order, keeping `app.py` with the liveness half.

### Wave 2: four packages in parallel

| Package | Agent | Needs | Creates | Edits |
| ------- | ----- | ----- | ------- | ----- |
| **P10 Config reloader** | `developer` | P2, P4 | `chassis/server/config_loader.py`, `ctests/test_config_loader.py`, `poc/tests/test_config_reload.py`, `poc/tests/test_config_minio.py` (`network`), `poc/tests/fixtures/bad-configs/*.yaml`, `packages/chassis/schemas/chassis-config.v0.json` and `task-result.v1.json` (by `make schemas`) | `chassis/server/app.py` (second: start the reloader in the lifespan; `/ready` waits for the first config), `chassis/server/interfaces/limits.py` (`BodyLimit` reads the live limit), `chassis/schemas.py` |
| **P11 Idempotency wiring and client disconnect** | `developer` | P1, P6 | `ctests/test_idempotency_serve.py`, `ctests/test_client_disconnect.py` | `chassis/server/interfaces/ids.py` (`key_from`), `chassis/server/interfaces/serve.py` (begin, finish, replay, the header, the disconnect watcher), `chassis/server/pipeline.py` (`Run.seen`, `Run.done`, and `RunPipeline.on_finished`, a list of async callbacks run after a run ends, for P14), `chassis/server/interfaces/native.py`, `openai.py`, `anthropic.py` (pass `disconnected`) |
| **P12 Dapr adapter** | `developer` | P3 | `chassis/adapters/dapr/__init__.py`, `chassis/adapters/dapr/events.py`, `ctests/test_dapr_inbound.py` (offline: the routes, the token check, SUCCESS, RETRY, DROP), `suites/containers/dapr.py`, `ctests/integration/test_dapr_events_contract.py` | `chassis/server/proxy_app.py` (mount `inbound_routes` when present) |
| **P13 Load scripts** | `developer` | P7 | `poc/load/locustfile.py`, `poc/load/run_matrix.py`, `poc/load/steady_client.py`, `poc/load/kill_drill.py`, `poc/load/README.md` | — |

### Wave 3: features done, evidence starts

| Package | Agent | Needs | Creates | Edits |
| ------- | ----- | ----- | ------- | ----- |
| **P14 Result events** | `developer` | P3, P10, P11 | `chassis/server/results.py` (the publisher: `TaskResult`, `CloudEvent`, background tasks, the count), `ctests/test_result_events.py` | `chassis/server/app.py` (third: register the publisher on `pipeline.on_finished` when `spec.events.result_events`; wait for pending publishes at shutdown) |
| **P15 Scenario tests** | `tester` | P1, P3, P10, P11 | `poc/tests/poc04_harness.py` (two replicas on Unix sockets sharing `InMemoryState` and `InMemoryBus`), `poc/tests/test_idempotency.py`, `poc/tests/test_killed_replica.py`, `poc/tests/test_swap_drill.py`, `poc/tests/test_hidden_state.py`, `poc/tests/test_idempotency_valkey.py` (`network`) | — |
| **P16 Compose drills and the load matrix** | `tester` (runs Docker) | P7, P10, P11, P13 | `poc/tests/test_compose_scale.py` (`network`), `poc/notes/load/` (raw CSVs, `results.json`), `poc/notes/2026-10-xx-load-results.md`, `poc/notes/2026-10-xx-hidden-state.md`, `poc/tests/test_load_results.py` | — |
| **P17 kind drills** | `tester` (runs kind) | P4, P5, P8 | `poc/tests/test_kind.py` (`network`), `poc/notes/2026-10-xx-container-roles.md` | — |

### Wave 4: the Dapr comparison, and the optional consumer

| Package | Agent | Needs | Creates | Edits |
| ------- | ----- | ----- | ------- | ----- |
| **P18 Dapr against the broker client** | `tester`, with `platform-security` for the token check | P12, P14, P16 | `poc/notes/2026-10-xx-dapr-vs-broker.md` (daprd CPU and memory per replica at the load-test rate with result events on, the work to close its API, a 401 from the workload container, lines of code per side with `wc -l` over `adapters/kafka` and `adapters/dapr` plus the component YAML) | — |
| **P19 Event-triggered runs** (optional) | `developer` | P14 | `chassis/server/consumer.py`, `ctests/test_event_consumer.py` | `chassis/core/inbound.py` (`Interface` gains `event`), `chassis/server/app.py` (fourth: start and stop the consumer; stop it first on drain) |

### Wave 5: close

**P20 Close** (`chassis-architect` with `docs-editor`). Creates `docs/contracts/contract-v3.md`, `docs/planning/adr/004-<slug>.md` (with the `adr` skill, from P18's note), `poc/notes/backlog-changes.md`, `poc/tests/test_records.py`, `poc/demo/`. Edits `packages/chassis/CLAUDE.md` (the new layout), `poc/README.md` (boxes and evidence), `poc/CLAUDE.md`, issues 001 DEC-1, 019 H-17, 024 CH-3, 010 H-12, 018 H-18 (status after PoC-4), and `docs/planning/poc/000-plan.md`; then `make planning-sync planning-check` and `make check`.

### Shared files: who edits, in which order

| File | Order |
| ---- | ----- |
| `chassis/profiles.py`, `chassis/ports/bundle.py`, `chassis/ports/__init__.py`, `chassis/server/config.py`, all `pyproject.toml`, `uv.lock`, `Makefile` | P0 only |
| `chassis/server/app.py` | P4 → P10 → P14 → P19 |
| `chassis/core/inbound.py` | P6 → P19 |
| `chassis/fakes/__init__.py` | P0 → P3 |
| `chassis/server/pipeline.py`, `server/interfaces/serve.py`, `ids.py`, `native.py`, `openai.py`, `anthropic.py` | P11 only |
| `chassis/server/interfaces/limits.py`, `chassis/schemas.py` | P10 only |
| `chassis/server/proxy_app.py` | P12 only |
| `chassis/server/cli.py`, `chassis/ports/engine.py`, `chassis/adapters/a2a/*` | P4 only |
| `suites/config.py` | P2; `suites/engine.py`: P4 |
| `packages/chassis/configs/` | P0 (`local.yaml`, `sidecar.yaml`), P7 (new `scale*.yaml`) |
| `deploy/compose/*` | P7; `deploy/kind/*` and `deploy/README.md`: P8 |
| `packages/chassis/CLAUDE.md`, `docs/contracts/`, `poc/README.md` | P20 only |
| `ctests/test_contracts.py` | nobody: each new binding is a file of its own |

## 13. Risks and fallbacks

- **Memory on this machine.** Four pairs, Kafka, MinIO, Valkey, and Traefik next to the running `paligo-*` stacks in a 7.7 GiB Docker VM. Fallback: run the matrix without the `events` profile, and the Dapr comparison at 1 pair only.
- **The daprd callback from a testcontainer** may not reach the test process. Fallback in section 10.
- **Traefik's keep-alive to a draining chassis** may race the listener close and reset one request. If the drill sees it, add `--drain-delay-s` time first, then Traefik's `retry` middleware for connection errors only, and record which one fixed it.
- **The fake model server or Locust saturates first.** Then RPS stops growing for every engine at once; the note flags it and the matrix reruns with fewer users per pair or a second fake model server.
- **A framework keeps hidden state** (LangGraph checkpointers, PydanticAI message history). The kept-data test finds it; the note lists it with a fix or a reject, as the exit criterion asks.
