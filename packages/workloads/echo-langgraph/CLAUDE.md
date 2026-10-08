# packages/workloads/echo-langgraph

The simplifier as a LangGraph graph. `packages/workloads/CLAUDE.md` still applies.

- One public thing: `echo_langgraph.handle`, the wire form of the contract. `workload-a2a serve --handle echo_langgraph:handle` serves it.
- Never import `chassis`. `make lint` (import-linter) enforces it.
- Framework code lives in `handle.py` (the graph) and `tools.py` (MCP to LangChain tools). The event mapping lives only in `mapping.py`, so its line count stays the measure PoC-2 records.
- No key. The model client gets the placeholder `not-a-key`; never read an `*_API_KEY` variable. `CHASSIS_API_TOKEN` (remote lane), when set and not empty, is the model's `api_key` and the MCP `Authorization: Bearer` header; never log it. Forward `ctx["traceparent"]` on every model and MCP call.
- Both HTTP paths are `httpx2` (openai 3, mcp 2), not `httpx` 0.28. The test hooks are `handle.model_transport` and `tools.transport`.
- `tools.py` stands in for `langchain-mcp-adapters` until a release for `mcp` 2 is locked. Then `load_tools` becomes `MultiServerMCPClient(...).get_tools()`.
- Keep `SYSTEM_PROMPT` and `PROMPT_VERSION` equal to echo-python's; bump both together.
