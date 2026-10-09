# echo-smolagents

Skeleton for PoC-6 (6b). The simplifier as a smolagents (code agent) agent, in the `remote` lane (trust: `untrusted`). It is not built yet: `handle` yields `start`, then `error` with code `not_implemented`. A later PoC-6 task fills in the engine.

## Shape

- `src/echo_smolagents/handle.py`: `handle(input: dict, ctx: dict) -> AsyncIterator[dict]`, the wire form. `transport` is the test-only model hook (`httpx2`).
- `src/echo_smolagents/tools.py`: `transport` is the test-only MCP hook (`httpx2`).
- No model key. The model URL and the tool URL are the chassis's proxies (`CHASSIS_MODEL_URL`, `CHASSIS_TOOL_URL`).

## Run

```bash
uv run workload-a2a serve --handle echo_smolagents:handle --port 9000
docker build -f packages/workloads/echo-smolagents/Dockerfile -t echo-smolagents .   # from the repo root
```

## Test

```bash
scripts/check_offline.sh packages/workloads/echo-smolagents -q
```

`tests/test_echo_smolagents_skeleton.py` checks that the skeleton's events validate against `packages/chassis/schemas/events.v0.json`.
