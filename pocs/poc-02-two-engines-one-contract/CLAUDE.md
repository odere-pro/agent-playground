# pocs/poc-02-two-engines-one-contract

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply. Status: done (2026-10-01).

## What is here

- `README.md`: the question, the scope and exit-criteria checklists with evidence per box, the gate tails, how to run, and the notes index. The source of truth for what this iteration proved.
- `tests/`: one scenario test per exit criterion, docstring names it. `test_engines.py` (the four engines, the `EnginePort` suite, response shape), `test_lanes.py` (`sidecar` against `inprocess`, the lane suite, the TypeScript twin), `test_tools.py`, `test_run_correlation.py`, `test_boundaries.py` (no framework, no key), `test_compose_sidecar.py`, `test_records.py`. `poc02_harness.py` routes a workload's HTTP back into the chassis app.
- `demo/`: `measure.py` (the measurements script) and `2026-10-01-demo-sidecar.md` (the Compose demo record).
- `notes/`: the open decisions, the measurements, the debt, and `backlog-changes.md`.

## What it built (lives in `packages/` and `deploy/`, not here)

- `packages/chassis/src/chassis/adapters/a2a`: `connector.py` (shared client), `sidecar.py`; `adapters/mcp`; `ports/tool.py`, `fakes/tool.py`; `server/proxy_app.py`, `correlation.py`, `tool_endpoint.py`; `core/trace.py`. Spec: `docs/contracts/contract-v1.md`.
- `packages/workload-a2a`: the Python template server. `packages/workloads/echo-{python,pydanticai,langgraph,typescript}`: the four workloads.
- `packages/contract-suites`: `chassis_contracts/lane.py`, `tool.py`, and the new engine and model cases.
- `deploy/compose/docker-compose.sidecar.yaml`, `demo-sidecar.sh`; `packages/chassis/configs/sidecar.yaml`.

## How to work with it

- Read first: `docs/planning/poc/002-PoC-2-two-engines-one-contract.md`, then `README.md`, then `docs/contracts/contract-v1.md`.
- The request path, in order: `/v1/run` → `SidecarConnector` (or `InProcessConnector`) → A2A on `127.0.0.1:9000` → `workload_a2a` `HandleExecutor` → the workload's `handle` → model call to `127.0.0.1:8090/v1/chat/completions` and tool call to `127.0.0.1:8090/mcp`, each with `traceparent` → `RunRegistry` charges the run → `ports.model` (LiteLLM) or `ports.tools` → events back up. Start from `chassis/server/app.py`.
- `make test-poc POC=02` is the gate. TCP and Docker: `uv run pytest pocs/poc-02-two-engines-one-contract -m network -p no:socket -q`.
- A box in `README.md` changes only with evidence. Known debt is in `notes/2026-10-01-debt.md`; do not fix it here.

## Do not touch

- `mapping.py` in one copy only: the chassis and `workload_a2a` copies must stay byte-equal.
- The scope of another iteration: scoped keys and egress (PoC-5), the bake-off (PoC-6). Record it under `notes/` and stop.

## Ask

`chassis-architect` before a contract change, `platform-security` for keys, egress, or images, `observability-expert` for spans and correlation, `eval-expert` for scoring.
