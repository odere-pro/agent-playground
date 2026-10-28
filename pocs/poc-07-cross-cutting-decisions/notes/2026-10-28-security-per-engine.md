# Security per engine, 2026-10-28 (exit criterion 6, security part)

The question: for security, what must live in each workload's event mapping? The answer for every engine so far: **nothing**. Every security hook sits in the chassis or in a shared service, so a new engine gets it with no code.

## Where each hook lives

| Hook | Where | Engine code |
| ---- | ----- | ----------- |
| Caller auth and scopes (OIDC) | Chassis public app (`server/security.py`), every route but `/health` and `/ready` | None |
| The `/v1/mcp` hop to `/v1/run` | Chassis: the inner call carries the MCP caller's own token and is checked again | None |
| `input` guardrail | Chassis, before the engine runs (`server/guard.py`, `Run`) | None |
| `tool_output` guardrail | Chassis tool endpoint, `/mcp` on the proxy port (`GuardedTools`) | None |
| `output` guardrail (complete calls) | Chassis, after the engine ends | None |
| Guardrails on every model call (Presidio, Prompt Guard 2) | LiteLLM, by the service's key | None |
| Keys | Chassis container only; the model proxy never forwards them | The workload must not hold a key |

## What each engine must expose

| Engine | Must do | Must not do |
| ------ | ------- | ----------- |
| `echo-python` | Call the model and tools only through `CHASSIS_MODEL_URL` and `CHASSIS_TOOL_URL`; forward `traceparent` | Hold a key or read one from its environment |
| `echo-langgraph` | The same | The same |
| `echo-pydanticai`, `echo-openai-agents`, `echo-smolagents`, `echo-claude-agent`, `echo-typescript` | The same (not run in the PoC-7 security tests; the chassis code is the same) | The same |

`traceparent` matters for security because the tool endpoint ties a write tool's idempotency key to the run it names. Without it a write is refused (`idempotency_key_required`).

## How an engine reacts to a blocked tool output

This is engine behavior, not a security hook. A blocked tool output is an MCP tool error (`guardrail_blocked`, fixed text). `echo-python` ends the run with its own `tool_error` event. `echo-langgraph` may differ (not checked here). In both, the injected text reaches neither the model nor the answer (`test_poc07_sec_engines.py::test_tool_output_injection_never_reaches_the_workload`).

## Evidence

- `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_sec_engines.py`: `echo-python` and `echo-langgraph`, `inprocess` and `sidecar` (A2A over a Unix socket), one chassis and the same ports in every cell.
- Offline only. The guardrail classifier is the pattern stand-in (see `2026-10-28-redteam.md`).
