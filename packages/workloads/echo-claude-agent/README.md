# echo-claude-agent

Skeleton for PoC-6 (6b). The simplifier as a Claude Agent SDK agent, in the `remote` lane (trust: `untrusted`). It is not built yet: `handle` yields `start`, then `error` with code `not_implemented`. A later PoC-6 task fills in the engine.

## Shape

- `src/echo_claude_agent/handle.py`: `handle(input: dict, ctx: dict) -> AsyncIterator[dict]`, the wire form. `transport` is the test-only model hook (`httpx2`).
- `src/echo_claude_agent/tools.py`: `transport` is the test-only MCP hook (`httpx2`).
- No model key. The model URL and the tool URL are the chassis's proxies (`CHASSIS_MODEL_URL`, `CHASSIS_TOOL_URL`).

## Run

```bash
uv run workload-a2a serve --handle echo_claude_agent:handle --port 9000
docker build -f packages/workloads/echo-claude-agent/Dockerfile -t echo-claude-agent .   # from the repo root
```

## Test

```bash
scripts/check_offline.sh packages/workloads/echo-claude-agent -q
```

`tests/test_echo_claude_agent_skeleton.py` checks that the skeleton's events validate against `packages/chassis/schemas/events.v0.json`.
