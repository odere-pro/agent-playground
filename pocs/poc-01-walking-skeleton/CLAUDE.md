# pocs/poc-01-walking-skeleton

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply. Status: done (2026-09-29).

## What is here

- `README.md`: the question, the scope and exit-criteria checklists with evidence per box, the demo, and the notes index. The source of truth for what this iteration proved.
- `HOW-TO-RUN.md`: what to install, then how to run the tests, one chassis process, and the Compose stack, with the `curl` calls.
- `tests/`: one scenario test per exit criterion, docstring names the criterion. `test_day0.py` (ports, fakes, schemas, import rules), `test_serve.py` (`chassis serve`, `/v1/run` streaming and complete, `versions`), `test_a2a_inprocess.py` (the workload over A2A in memory), `test_model_adapter.py` (LiteLLM adapter and the config switch), `test_compose.py` (the Compose files offline; the live stack under the `network` marker).
- `demo/`: the recorded run of `deploy/compose/demo.sh`.
- `notes/`: dated debt notes (keys in Compose, the in-memory lane), and `backlog-changes.md` for what the backlog issues should change.

## What it built (lives in `packages/` and `deploy/`, not here)

- `packages/chassis/src/chassis/server`: `chassis serve`, `/v1/run`, `/health`, `/ready`, `ChassisConfig`. Since PoC-2 the model proxy `/v1/chat/completions` is on the localhost-only proxy listener (`proxy_app.py`, `127.0.0.1:8090`, suggested).
- `packages/chassis/src/chassis/adapters/a2a`: the event-to-A2A mapping, the template A2A server, the `inprocess` connector. Spec: `docs/contracts/contract-v0.md` (the PoC-1 record; v1 is current); placement: ADR-002.
- `packages/chassis/src/chassis/adapters/litellm`: `LiteLLMModel`, the first real `ModelPort`.
- `packages/workloads/echo-python`: the simplifier `handle`. Never imports `chassis`.
- `deploy/compose`: the stack, `SECURITY.md`, `demo.sh`. `packages/chassis/configs/{fake,local}.yaml`: the two configs (`fake.yaml` names `spec.engine.connector: inprocess`).

## How to work with it

- Read first: `docs/planning/poc/001-PoC-1-walking-skeleton.md`, then `README.md` here (its "What we built" section has the diagram and the plain explanation), then `HOW-TO-RUN.md`.
- The request path, in order: `/v1/run` → `InProcessConnector` → A2A in memory → `HandleExecutor` → `echo_python.handle` → back into the chassis at `127.0.0.1:8090/v1/chat/completions` (the proxy listener) → `ports.model` → (LiteLLM) → events back up. Start from `chassis/server/app.py` and follow it down.
- A box in `README.md` changes only with evidence: a test name or a command and its output.
- Before a commit: `make quick`. Before a PR: `make check`. Live checks: `uv run pytest -m network -p no:socket`.
- Known debt is in `notes/`; do not fix it here. PoC-2 paid the sidecar lane, per-delta streaming over it, `traceparent`, and the localhost-only proxy; PoC-5 owns the scoped router keys.
- Do not touch: the scope of another iteration. If a criterion needs it, record it under `notes/` and stop.
- Ask: `chassis-architect` before a contract change (events, envelope, the A2A mapping), `platform-security` before anything with keys or egress.
