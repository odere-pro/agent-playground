# pocs/poc-02-two-engines-one-contract

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply. Status: done (2026-10-01).

## What is here

- `README.md`: the question, the scope and exit-criteria checklists with evidence per box, the gate tails, how to run, and the notes index. The source of truth for what this iteration proved.
- `tests/`: one scenario test per exit criterion, docstring names it. `test_engines.py` (the four engines, the `EnginePort` suite, response shape), `test_lanes.py` (`sidecar` against `inprocess`, the lane suite, the TypeScript twin), `test_tools.py`, `test_run_correlation.py`, `test_boundaries.py` (no framework, no key), `test_compose_sidecar.py`, `test_records.py`. The shared harness (apps, outbound routing, sockets, the TypeScript echo) is `packages/poc-harness` (`poc_harness`).
- `demo/`: `measure.py` (the measurements script) and `2026-10-01-demo-sidecar.md` (the Compose demo record).
- `notes/`: the open decisions, the measurements, the debt, and `backlog-changes.md`.

## What it built (lives in `packages/` and `deploy/`, not here)

- `packages/chassis/src/chassis/adapters/a2a`: `connector.py` (shared client), `sidecar.py`; `adapters/mcp`; `ports/tool.py`, `fakes/tool.py`; `server/proxy_app.py`, `correlation.py`, `tool_endpoint.py`; `core/trace.py`. Spec: `docs/contracts/contract-v1.md`.
- `packages/workload-a2a`: the Python template server. `packages/workloads/echo-{python,pydanticai,langgraph,typescript}`: the four workloads.
- `packages/contract-suites`: `chassis_contracts/lane.py`, `tool.py`, and the new engine and model cases.
- `deploy/compose/docker-compose.sidecar.yaml`, `demo-sidecar.sh`; `packages/chassis/configs/sidecar.yaml`.

## How to work with it

- Read first: `docs/planning/poc/002-PoC-2-two-engines-one-contract.md`, then `README.md`, then `docs/contracts/contract-v1.md`.
- The request path, in order: `POST /v1/run` (`chassis/server/interfaces/native.py`) → `RunPipeline` (`chassis/server/pipeline.py`) → `SidecarConnector` (or `InProcessConnector`) → A2A on `127.0.0.1:9000` → `workload_a2a` `HandleExecutor` → the workload's `handle`.
- The calls back, each with `traceparent`: the model call to `127.0.0.1:8090/v1/chat/completions` (`chassis/server/model_proxy.py`), and the tool call to `127.0.0.1:8090/mcp` (`chassis/server/tool_endpoint.py`). `RunRegistry` charges the run, then `ports.model` (LiteLLM) or `ports.tools` serves it, and the events go back up.
- Start reading from `chassis/server/interfaces/native.py`, where the `/v1/run` route is.
- `make test-poc POC=02` is the gate. TCP and Docker: `uv run pytest pocs/poc-02-two-engines-one-contract -m network -p no:socket -q`.
- A box in `README.md` changes only with evidence. Known debt is in `notes/2026-10-01-debt.md`; do not fix it here.

## Do not touch

- `mapping.py` in one copy only: the chassis and `workload_a2a` copies must stay byte-equal.
- The scope of another iteration: scoped keys and egress (PoC-5), the bake-off (PoC-6). Record it under `notes/` and stop.

## Ask

`chassis-architect` before a contract change, `platform-security` for keys, egress, or images, `observability-expert` for spans and correlation, `eval-expert` for scoring.
