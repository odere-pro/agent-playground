# Contract v0

The written contract PoC-1's exit criterion asks for. Generated schemas: `packages/chassis/schemas/*.v0.json` (`make schemas`). Source models: `packages/chassis/src/chassis/core/`. Status: day 0. The A2A mapping is added by the PoC-1 walking skeleton and becomes part of v1 in PoC-2.

## The `handle` contract

```python
async def handle(input: TaskInput, ctx: Context) -> AsyncIterator[Event]: ...
```

Every service implements it in its workload. It yields chassis events. It is served over A2A in every lane: in memory in `inprocess`, on localhost in `sidecar`, over the network in `remote`.

## Events (`events.v0.json`)

A discriminated union on `type`. Every event carries `schema_version: "0"`. Unknown fields are refused. The chassis accepts the current and the previous major version.

| Event | Fields | Meaning |
| ----- | ------ | ------- |
| `start` | `request_id` | The first event, once |
| `delta` | `text` | A piece of the answer; deltas join to the full text |
| `tool_call` | `call_id`, `name`, `arguments`, `result?` | A tool the workload called through the chassis |
| `metrics` | `input_tokens`, `output_tokens`, `cost_usd?`, `model_route?`, `latency_ms?`, `attempt` | Usage per attempt; the collector sums tokens and cost |
| `end` | `status: ok | retry | fallback`, `output?` | The last event on success; `output` replaces the joined text when set |
| `error` | `code`, `message`, `retryable` | The last event on failure; the response gets `status: error` |

## Envelope (`request.v0.json`, `response.v0.json`)

From the epic's G.1, plus `versions` on every response.

- `Request`: `request_id`, `trace_id`, `idempotency_key`, `agent`, `agent_version`, `input: TaskInput`, `context_ref?`, `stream`, `budget {max_tokens, timeout_ms}`.
- `Response`: the same identity fields, `output`, `metrics`, `status: ok | retry | fallback | error`, `versions {chassis, config?, prompt?, model_route?}`, `context_ref?`.
- `TaskInput`: `text?`, `data {}`. `context_ref` is accepted and passed through, unused until shared memory.

## Context (`context.v0.json`)

What the chassis gives a `handle` next to the input: the identity fields, `budget`, `versions`, `model_route?`. It never holds a key.

## Ports (day 0)

| Port | Promise | Fake | Real adapter (arrives in) |
| ---- | ------- | ---- | ------------------------- |
| `ModelPort` | `complete` and `stream` agree; the last chunk carries `usage`; errors are `ModelError` | `ScriptedModel` | LiteLLM over OpenAI-compatible HTTP (PoC-1 walking skeleton) |
| `EngineConnector` | `kind` is a lane; `run` streams `start` first and `end` or `error` last; cancel is clean | `FakeEngine` (a test double, not a lane) | `inprocess` A2A in memory (PoC-1), `sidecar` (PoC-2), `remote` (PoC-5) |
| `ConfigPort` | `load` gives a version; a change gives a new version and notifies subscribers | `InMemoryConfig` | MinIO or S3 (PoC-4) |
| `TelemetryPort` | one span per call with attributes and parent; counters | `InMemoryTelemetry` | OpenTelemetry SDK (PoC-7) |

The other ports (`StatePort`, `EventPort`, `ToolPort`, `AuthPort`, `GuardrailPort`, `EvaluatorPort`, `FeedbackPort`, `RegistryPort`) arrive with their first adapter, in the same shape.

## `EngineConnector`, draft

```python
class EngineConnector(Protocol):
    kind: Literal["inprocess", "sidecar", "remote"]
    capabilities: frozenset[str]   # streaming, tools, structured_output, code_exec, multi_agent
    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None: ...
    def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]: ...
    async def close(self) -> None: ...
```

`inprocess` is allowed only in the `fake` and `local` profiles (`chassis.profiles.check_lane`).

## Profiles

`spec.adapters {model, engine, config, telemetry}`. Profiles `fake`, `local`, `cloud` set them all; an agent's own `spec.adapters` overrides per port. `build_ports(profile)` returns a `PortBundle` or raises `AdapterNotAvailable` naming the PoC that adds the missing adapter.
