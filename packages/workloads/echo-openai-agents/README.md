# echo-openai-agents

Skeleton for PoC-6 (6a). The simplifier as a OpenAI Agents SDK agent, in the `sidecar` lane (trust: `trusted`). It is not built yet: `handle` yields `start`, then `error` with code `not_implemented`. A later PoC-6 task fills in the engine.

## Shape

- `src/echo_openai_agents/handle.py`: `handle(input: dict, ctx: dict) -> AsyncIterator[dict]`, the wire form. `transport` is the test-only model hook (`httpx2`).
- `src/echo_openai_agents/tools.py`: `transport` is the test-only MCP hook (`httpx2`).
- No model key. The model URL and the tool URL are the chassis's proxies (`CHASSIS_MODEL_URL`, `CHASSIS_TOOL_URL`).

## Run

```bash
uv run workload-a2a serve --handle echo_openai_agents:handle --port 9000
docker build -f packages/workloads/echo-openai-agents/Dockerfile -t echo-openai-agents .   # from the repo root
```

## Test

```bash
scripts/check_offline.sh packages/workloads/echo-openai-agents -q
```

`tests/test_echo_openai_agents_skeleton.py` checks that the skeleton's events validate against `packages/chassis/schemas/events.v0.json`.
