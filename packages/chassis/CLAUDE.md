# packages/chassis

The service chassis. It serves one workload's `handle` behind the public interfaces and holds every credential.
Spec: `docs/contracts/contract-v7.md` (PoC-8's additive changes; v6 and earlier for the rest). Per-module detail: `docs/guides/chassis-reference.md`. Read it before you change a module.

## Where things live

Under `src/chassis/`:

- `core/` the envelope, events, `collect()`, `handle`, the inbound seam, the manifest, the result payload.
- `ports/` one `typing.Protocol` per dependency, bundled in `PortBundle`.
- `fakes/` in-memory doubles for every port.
- `adapters/` real adapters, one package per product, plus the A2A lane, MCP, and the chat wire formats.
- `server/` the public app, the proxy app, the pipeline, the interfaces, idempotency, reload, readiness, the drain.
- `profiles.py` `spec.adapters`, the profiles, `build_ports`, `check_lane`, `REGISTRY`.
- `schemas.py` the JSON Schema generator.

Under `packages/chassis/`: `configs/` the bundled specs, `schemas/` the published JSON Schemas, `tests/`.

## Import rules

- `core` has no network, no product SDK, and no web framework. `make lint` enforces it.
- A product SDK is imported only under `adapters/`. That covers `openai`, `anthropic`, a2a-sdk, and FastMCP.
- `valkey`, `aiokafka`, and `minio` are imported only under `adapters/`, inside a lazy factory. Never in `core` or `ports`.
- The OpenTelemetry SDK and pyjwt are imported only in their adapters.
- `packages/workload-a2a` never imports `chassis`. Its `mapping.py` is a byte-for-byte copy of `adapters/a2a/mapping.py`.

## Rules

- A change to an event or envelope field is a contract change. Ask `chassis-architect`. Bump `schema_version` on a breaking change, keep the previous major accepted, and run `make schemas`.
- Every new port gets a fake and a suite in `packages/contract-suites` in the same change (skill `contract-suite`).
- `inprocess` is for the chassis's own tests and local runs. `check_lane` refuses it elsewhere.
- Two chat routes share a path. Never write it bare. Say "the OpenAI interface (public port)" or "the model proxy (proxy port)".
- Two `/v1/messages` routes exist too. The Anthropic interface (public port) calls the agent. The Anthropic proxy route (proxy port, `server/model_proxy_messages.py`) calls the model. The proxy port serves `/v1/chat/completions`, `/v1/messages`, and `/mcp`, and the remote listener serves exactly those three.
- `/v1/mcp` on the public port is the agent as a tool. `/mcp` on the proxy port is the tools for workloads.
- Credentials stay in the chassis. The model proxy never forwards `Authorization` or `x-api-key`. The LiteLLM key is never logged.
- OpenAI and Anthropic errors send `public_message(code)`, never the run's own message. The detail stays in the log and the span.
- A replica keeps nothing a retry needs. What must outlive a call goes behind `StatePort`, `ConfigPort`, or `EventPort`. Never a module global or `app.state` (exit criterion 8; `pocs/poc-04-stateless-scalable/notes/2026-10-01-hidden-state.md`).
- A new `spec.*` field is restart-only unless it is in `RELOADABLE` and read per request from `state.config`. A reload never changes a run that is already open.
- Idempotency applies only to a key the client sent. Only a run that ended with `end` is cached. A keyed call fails closed with 503 `state_unavailable` when the store fails (suggested).
- Shutdown order is fixed: `/ready` 503, the drain delay, the public listener, in-flight runs, pending result events, the ports, then the proxy listener last.
- Signals belong to `lifecycle`, never to uvicorn.
- Result events never change a response and are never awaited by the request.

## Test

- `make test`, or `scripts/check_offline.sh packages/chassis` for this package only. Both run offline with no keys. A bare `uv run pytest` does not.
- Port bindings: `tests/test_contracts.py`. Inbound bindings: `tests/test_inbound_contract.py`.
- Real adapters: `tests/integration/`, marked `network`, run by `make test-integration` (Docker). Never in the gate.
- `make schemas` after a schema change. A test fails on drift.
- `make lint` checks the import rules. `make quick` before every commit.
- PoC-5 scenarios: `pocs/poc-05-sandboxed/tests/`. The kind ones run with `POC05_KIND=1`.
- PoC-6 scenarios: `pocs/poc-06a-bake-off-sidecar-lane/tests/`, `pocs/poc-06b-bake-off-remote-lane/tests/`, `pocs/poc-06c-pretrained-slm/tests/`. The kind ones run with `POC06_KIND=1`.
- PoC-7 scenarios: `pocs/poc-07-cross-cutting-decisions/tests/`. The stack drills run with `POC07_STACK=1`.
- PoC-8 scenarios: `pocs/poc-08-cross-cutting-build/tests/`. The stack drills run with `POC08_STACK=1` and `POC08_LITELLM=1`.
- The full test map is in `docs/guides/chassis-reference.md`.
