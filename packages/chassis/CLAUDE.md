# packages/chassis

The service chassis. It serves one workload's `handle` behind the public interfaces and holds every credential.
Spec: `docs/contracts/contract-v4.md` (PoC-5's additive changes; v3 and earlier for the rest). Per-module detail: `docs/guides/chassis-reference.md`. Read it before you change a module.

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
- `packages/workload-a2a` never imports `chassis`. Its `mapping.py` is a byte-for-byte copy of `adapters/a2a/mapping.py`.

## Rules

- A change to an event or envelope field is a contract change. Ask `chassis-architect`. Bump `schema_version` on a breaking change, keep the previous major accepted, and run `make schemas`.
- Every new port gets a fake and a suite in `packages/contract-suites` in the same change (skill `contract-suite`).
- `inprocess` is for the chassis's own tests and local runs. `check_lane` refuses it elsewhere.
- Two chat routes share a path. Never write it bare. Say "the OpenAI interface (public port)" or "the model proxy (proxy port)".
- `/v1/mcp` on the public port is the agent as a tool. `/mcp` on the proxy port is the tools for workloads.
- Credentials stay in the chassis. The model proxy never forwards `Authorization`. The LiteLLM key is never logged.
- OpenAI and Anthropic errors send `public_message(code)`, never the run's own message. The detail stays in the log and the span.
- A replica keeps nothing a retry needs. What must outlive a call goes behind `StatePort`, `ConfigPort`, or `EventPort`. Never a module global or `app.state` (exit criterion 8; `pocs/poc-04-stateless-scalable/notes/2026-10-01-hidden-state.md`).
- A new `spec.*` field is restart-only unless it is in `RELOADABLE` and read per request from `state.config`. A reload never changes a run that is already open.
- Idempotency applies only to a key the client sent. Only a run that ended with `end` is cached. A keyed call fails closed with 503 `state_unavailable` when the store fails (suggested).
- Shutdown order is fixed: `/ready` 503, the drain delay, the public listener, in-flight runs, pending result events, the ports, then the proxy listener last.
- Signals belong to `lifecycle`, never to uvicorn.
- Result events never change a response and are never awaited by the request.

## Test

- `make test` or `uv run pytest packages/chassis`. Tests run offline with no keys.
- Port bindings: `tests/test_contracts.py`. Inbound bindings: `tests/test_inbound_contract.py`.
- Real adapters: `tests/integration/`, marked `network`, run by `make test-integration` (Docker). Never in the gate.
- `make schemas` after a schema change. A test fails on drift.
- `make lint` checks the import rules. `make quick` before every commit.
- PoC-5 adds the `remote` lane (`adapters/a2a/remote.py`), the remote proxy listener (`server/remote_auth.py`), `ToolPort` write mode and the MCP gateway adapter (`adapters/mcp/gateway.py`), the uncorrelated cap, and `spec.trust`. Its scenarios are in `pocs/poc-05-sandboxed/tests/`; the kind ones run with `POC05_KIND=1`.
- The full test map is in `docs/guides/chassis-reference.md`.
