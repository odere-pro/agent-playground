# packages/workloads/echo-pydanticai

The simplifier as a PydanticAI agent. `packages/workloads/CLAUDE.md` still applies.

- One public thing: `echo_pydanticai.handle`, the wire form (dicts in, event dicts out, `schema_version: "0"`). Served by `workload-a2a serve --handle echo_pydanticai:handle`.
- Never import `chassis`; import-linter enforces it. Copy constants from echo-python, do not import it. Keep `SYSTEM_PROMPT` and `PROMPT_VERSION` equal to echo-python's.
- The event mapping lives only in `mapping.py`. Keep framework-to-chassis translation there so its line count stays meaningful.
- No model key. Never read an `*_API_KEY` variable. `CHASSIS_API_TOKEN` (remote lane), when set and not empty, is the openai `api_key` and the MCP client's `Authorization` header, and `_drop_authorization` is not installed; never log it. The openai SDK gets `PLACEHOLDER_API_KEY`, and `_drop_authorization` removes the header. Model and MCP URLs point at the chassis (`CHASSIS_MODEL_URL`, `CHASSIS_TOOL_URL`).
- Every model and MCP request carries `ctx["traceparent"]` as a header. Build both HTTP clients with `_client`, which sets it.
- No tool is defined here. The model sees what the chassis serves over MCP.
- A failure is one `error` event from `mapping.error_event`, never an exception out of `handle`.
- `model_transport` and `tool_transport` are test-only `httpx2` hooks. The openai SDK and the MCP client use `httpx2`, not `httpx`; an `httpx` transport does not work here.
- Bump `PROMPT_VERSION` when the prompt changes, in every simplifier at once.
