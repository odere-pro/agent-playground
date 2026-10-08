# packages/workloads/echo-python

The plain-Python simplifier workload, and the PoC-2 baseline for mapping size and token overhead. `packages/workloads/CLAUDE.md` still applies.

- One public thing: `echo_python.handle`, the wire form of the contract (dicts in, event dicts out, `schema_version: "0"`). The chassis loads it by dotted path from `spec.engine.handle`.
- Never import `chassis`. Dependencies: `httpx` and `mcp` (which brings `httpx2`). `make lint` (import-linter) enforces the chassis rule.
- Model calls go to `CHASSIS_MODEL_URL`, tools to `CHASSIS_TOOL_URL` (the chassis's proxies). No model key, ever. `CHASSIS_API_TOKEN` (remote lane only), when set and not empty, is the `Authorization: Bearer` header on every model and MCP call; unset, no header. Never log it.
- `ctx["traceparent"]`, when set, is the `traceparent` header on every model and MCP call, as is. Tests check both.
- Tools come from the chassis over MCP. Never hardcode a tool schema here. The loop is in `handle.py`; the MCP client and the OpenAI tool-loop messages are in `tools.py`. Keep both small: their line count is the plain-Python baseline.
- `MAX_TOOL_ROUNDS` caps the loop (suggested: 3). Codes: `tool_error` for a failed `tools/call`, `tool_loop_exceeded` past the cap. An unreachable tool endpoint at list time is not an error: the run goes on without tools and `tools.list_failures` counts it.
- The input text is a user message on its own. Do not put it in the system prompt; `tests/test_handle.py` checks it.
- Test-only hooks: `handle.transport` (the model call's httpx transport) and `tools.client_factory` (builds the httpx2 client the MCP SDK uses). Production code only passes them on.
- Open the FastMCP stub's lifespan inside the test (`serving(...)`), not in an async fixture: its task group must exit in the task it entered.
- Bump `PROMPT_VERSION` when the prompt changes; the chassis reports it in `versions.prompt` from config.
