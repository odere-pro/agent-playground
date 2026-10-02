# Backlog changes when PoC-2 closes

What the backlog issues should change once this iteration closes. Apply with skill `planning-sync`; issue bodies are edited by hand, order and dependencies in `docs/planning/tools/backlog.py`. The issues themselves are not edited here.

Evidence: `pocs/poc-02-two-engines-one-contract/README.md` (ticked boxes), `docs/contracts/contract-v1.md`, `notes/2026-10-01-measurements.md`, `notes/2026-10-01-debt.md`.

## 009 CH-1: engine connectors over A2A

- The `sidecar` connector exists (`chassis.adapters.a2a.sidecar.SidecarConnector`) and shares one client with `inprocess` (`connector.py`, `A2AConnector`). `spec.engine.url` is loopback only; `spec.engine.uds` is a Unix socket for tests. The issue's `## What` should say PoC-2 delivered it.
- `EngineConnector` is final for `inprocess` and `sidecar` (contract v1, "`EngineConnector`"). Link `docs/contracts/contract-v1.md` from the issue; it supersedes v0 for the mapping.
- Measurements for the issue: the sidecar hop over `inprocess` is 1.96 ms at p50 and 13.39 ms at p95; a streamed delta costs 315.6 µs at p50 in `sidecar`, under the plan's suggested 0.5 ms. Unix socket, fake model, one laptop; loopback TCP between containers is not measured.
- New error code: `a2a.transport` (suggested) for a sidecar that dies mid-stream; `a2a.bad_request` on the server.
- Open, from the debt note: the run deadline (the timeout is per read), mid-await cancel in the Python server, the JSON-RPC error answer that becomes `engine_error`, and the stale `a2a.unsupported_state` message.

## 013 CH-2: outbound model proxy

- Delivered: the proxy is on a localhost-only listener (`chassis serve --proxy-host 127.0.0.1 --proxy-port 8090`, suggested); it keys each call to its run by `traceparent` (`correlation.py`, `RunRegistry`) and refuses a run over `budget.max_tokens` with 429 `budget_exhausted`; two concurrent runs keep their own budgets.
- Delivered: `ModelMessage` carries the tool loop (`tool_calls`, `tool_call_id`, `content: null`), and the proxy refuses what it cannot carry with a 400 before the model is called (contract v1, change 3).
- Open: no per-replica budget for uncorrelated calls (they are served and counted); the per-call model timeout is not capped at the run's budget (needs a `ModelPort` change, with 012 H-3); the Anthropic format; a scoped key per service (PoC-5).

## 054 H-16: tool port

- Delivered: `ToolPort` (`chassis.ports.tool`), the fake `InMemoryTools` with `glossary_lookup` defined once (`chassis.fakes.tool`), `ToolPortContract`, and the MCP endpoint `/mcp` on the proxy listener (FastMCP 4, stateless). Every Python workload gets the tool from it.
- Open: the real adapter (`tools: mcp`, named for PoC-5 in `profiles.REGISTRY`), the MCP gateway behind it, and charging tool calls to the run (the endpoint records the `traceparent` but does not look up the run).

## 008 H-14: one agent interface

- Four workloads (plain Python, PydanticAI, LangGraph, TypeScript) run behind one `handle` contract and pass the same `EnginePort` suite and the same response-shape tests. `spec.engine.connector` is a typed field with default `sidecar`, and `inprocess` is refused outside `fake` and `local`. The issue can mark the lane field and the `inprocess` refusal as delivered.

## 025 H-10: template repo

- The template has two parts to draw on: `packages/workload-a2a` (the Python template server, `workload-a2a serve --handle module:attribute`) and the TypeScript port in `packages/workloads/echo-typescript/src/a2a_server.ts`.
- ADR-002 item 4 is amended: one shared package, not an `a2a_server.py` per workload. The issue should say the template depends on `workload-a2a` instead of copying files.
- Each workload Dockerfile from PoC-2 is a starting point; the Node base is not yet pinned by digest.

## 026 CH-4: chassis-only credentials and egress

- The negative suite for workload containers exists offline: no workload service, image, or source holds or reads a key, and the Compose overlay gives no workload an `env_file` or a port (`tests/test_boundaries.py`, `tests/test_compose_sidecar.py`). The live check, `deploy/compose/demo-sidecar.sh` printing each workload's environment, has not run (Docker down).
- The public port leaves localhost to the proxies in the shared namespace (`test_chassis_public_port_leaves_localhost_to_the_proxies`).

## 010 H-12: config loader

- Delivered: one lane key (`spec.engine.connector`; `spec.adapters.engine` is refused), `spec.adapters` merged over the profile defaults field by field, a `tools` field, and the `cloud` refusal of `fake` and `memory` adapters (suggested). The issue's open `cloud` plus `inprocess` refusal is delivered.
- Still open: MinIO source, hot reload, `risk_class` and `vault://` checks.

## Reuse analysis (`docs/planning/poc/010-reuse-analysis.md`)

- Add a watch-out: `langchain-mcp-adapters` 0.3.1 imports `mcp` 1.x modules that `mcp` 2.2.0 removed. `echo-langgraph` uses an 86-line stand-in over the `mcp` 2 client. Re-check at PoC-6a.
- Versions verified on 2026-10-01: a2a-sdk 1.2.0, `@a2a-js/sdk` 1.3.0, FastMCP 4.0.10, mcp 2.2.0, pydantic-ai-slim 2.52.0, langgraph 1.2.12, langchain-openai 1.6.7.
