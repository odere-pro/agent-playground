# packages/workloads/echo-openai-agents

PoC-6 (6a): the simplifier as an OpenAI Agents SDK agent (`openai-agents==0.23.1`). `packages/workloads/CLAUDE.md` still applies.

- One public thing: `echo_openai_agents.handle`, the wire form (dicts in, event dicts out, `schema_version: "0"`). Served by `workload-a2a serve --handle echo_openai_agents:handle`.
- Files: `handle.py` (the run), `mapping.py` (SDK events to chassis events, and `error_event`; keep it in its own file so its size can be counted), `tools.py` (the MCP server class). Test hooks: `handle.transport` (model) and `tools.transport` (MCP), both `httpx2` transports.
- Chat completions only: `OpenAIChatCompletionsModel`, never the Responses API. The `AsyncOpenAI` client has `max_retries=0`; the chassis owns retries.
- SDK tracing stays off (`set_trace_processors([])`, `set_tracing_disabled(True)`, `RunConfig(tracing_disabled=True)`). A test proves no processor or exporter runs. Do not add a processor.
- The tool loop is at most 3 rounds: `max_turns=4` plus the `on_llm_end` hook, which stops the 4th round before its tools run. A failed tool is `tool_error`, not text for the model.
- Router compatibility is pinned in `tests/test_oai_router_compat.py`. The workload sends only `model`, `messages`, `tools`, `stream`. If you set a `ModelSettings` field, check the proxy keeps its key first (`model`, `messages`, `temperature`, `max_tokens`, `tools`, `stream`) and update the README table.
- Never import `chassis`; import-linter enforces it. Copy constants from echo-python, do not import it.
- No model key. Never read an `*_API_KEY` variable (the client gets the placeholder `not-a-key`, and the `Authorization` header is dropped unless `CHASSIS_API_TOKEN` is set). Model and MCP URLs point at the chassis.
- Lane `sidecar`, trust `trusted`. Image uid 10002.
- A failure is one `error` event, never an exception out of `handle`.
- The tests import `fake_mcp_server` and `fake_model_server` from the workspace; the package's dev group lists only `fake-model-server` and `fastmcp`. Adding `fake-mcp-server` there needs a `uv.lock` change outside this folder.
