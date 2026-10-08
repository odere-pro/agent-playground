# Backlog changes when PoC-1 closes

What the backlog issues should change once this iteration closes. Apply with skill `planning-sync`; issue bodies are edited by hand, order and dependencies in `docs/planning/tools/backlog.py`. The issues themselves are not edited here.

Evidence: `pocs/poc-01-walking-skeleton/README.md` (ticked boxes), `demo/2026-09-29-demo-fake-variant.md`, `notes/2026-09-29-compose-key-debt.md`.

## 009 CH-1: engine connectors over A2A

- The template A2A server and the `inprocess` connector exist under `chassis.adapters.a2a` (`mapping.py`, `server.py`, `inprocess.py`). The issue's `## What` should say PoC-1 delivered them and that CH-1 adds `sidecar` and the measurements.
- ADR-002 decides placement: the workload never depends on `chassis`, sees `handle` as dicts in and dicts out, and PoC-2 copies `mapping.py` and `server.py` into the service template. Link ADR-002 from the issue.
- a2a-sdk 1.2 detail: the first stream response is the task itself, in `SUBMITTED` state; the chassis events ride in `status_update` and `artifact_update` metadata under `EVENT_KEY`, and the last update is `TASK_STATE_COMPLETED`. Write this into the mapping so the sidecar copy matches.
- The criterion "`inprocess` opens no socket" already holds (`make test` runs with sockets disabled). Mark it as delivered rather than open.

## 013 CH-2: outbound model proxy

- A thin `/v1/chat/completions` pass-through already exists in `chassis.server.model_proxy`; the in-process workload calls it with `CHASSIS_MODEL_URL`, and it forwards to the `ModelPort` adapter (the fake in the `fake` profile, LiteLLM in `local`). CH-2 starts from it instead of a blank page.
- What it lacks and PoC-2 adds: `traceparent` correlation, per-request budgets, the Anthropic format, and the sidecar case where the workload container holds no key.

## 002 G-1: LiteLLM router

- LiteLLM runs in Compose, pinned by digest, with two routes, and starts with `docker compose up --wait` and a health check. The `fake` variant runs with no master key at all, because an empty `master_key` in LiteLLM 1.103.0 turns auth on and 401s every call.
- Debt: in the `local` variant `LITELLM_API_KEY` equals `LITELLM_MASTER_KEY`, which fails the criterion "no service config holds the master key". Deadline PoC-5 (one scoped key per service); it blocks PoC-7 budgets. Note this under `## Out of scope` with the deadline.
- Only the OpenAI format is served today; `/v1/messages`, Claude, and Gemini are untested.

## 003 G-1b: named model routes

- `big-default` and `local-small` answer by name; switching `spec.model.route` is a config change and a restart (demo). The issue's second route is named `simplifier-slm`; decide whether `local-small` is renamed or kept as a third route.
- Not yet: tool-call declaration per route, route versions in config history, and the clear error for an unknown route.

## 004 G-2: token and cost counting

- Token counts per call are visible in the router today through a custom callback (`deploy/compose/litellm/token_log.py`) that prints one line per call with route, tokens, cost, and `tags=agent:<name>`, because there is no Postgres and `/spend/logs` answers `No connected db.` Streamed calls log real counts, not zero.
- The agent tag comes from the request metadata, so it is spoofable until a virtual key carries it. The criterion "counted under the agent of its key" stays open and needs the virtual-key work.
- Cost is `0.000000` for the fake model; a real cost figure is unverified until the `local` override runs.

## 012 H-3: model port

- `LiteLLMModel` (`chassis.adapters.litellm`) exists, is picked by `spec.adapters.model: litellm`, and binds `ModelPortContract` next to the fake (`packages/chassis/tests/test_contracts.py::TestLiteLLMModel`). Offline it runs against the fake model server over an ASGI transport.
- Delivered: route switch by config, tokens in the `metrics` event, route in `versions`. Open: direct vLLM and llama.cpp adapters, the virtual-key spend test, temperature default, and the `cloud` profile refusal.

## 011 H-2: inbound adapters

- The native endpoint exists: `POST /v1/run` with `stream: false` (one envelope) and `stream: true` (server-sent events). The SSE frame format is one `event: <type>` / `data: <json>` frame per chassis event, then a final `response` frame carrying the collected envelope. Write this format into the issue so the OpenAI and Anthropic adapters map from it.
- `/health` and `/ready` exist; `/ready` is 503 until the app's lifespan has built the ports. The readiness-check registry, SIGTERM drain, and the sidecar cases are still open.

## 010 H-12: config loader

- `ChassisConfig` (`chassis.server`) exists: `profile`, `agent`, `spec.adapters`, `spec.engine`, `spec.model`, `spec.prompt`, and a `version` that is the file's content hash. Every response already reports `versions.config` and `versions.prompt`. Delivered criteria: the `fake` and `litellm` adapter switch, the `fake` profile with no network or keys, `versions` in the response.
- Open: MinIO source, hot reload, the `risk_class` and `vault://` checks, and the `cloud` plus `inprocess` refusal.

## 026 CH-4: chassis-only credentials and egress

- In PoC-1 the workload rides in the chassis image, PoC-1 only, because the lane is `inprocess`. The chassis container is the only one with `LITELLM_API_KEY`; the fake model server has no `LITELLM_*` variable (checked live). The negative suite for the workload container starts in PoC-2 when the sidecar arrives.
