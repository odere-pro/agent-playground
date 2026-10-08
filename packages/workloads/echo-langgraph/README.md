# echo-langgraph

The simplifier as a LangGraph graph (PoC-2). `handle(input, ctx)` takes the input text, asks a model to rewrite it in plain words, may call the chassis's `glossary_lookup` tool, and streams the answer back as chassis events.

## Shape

- `src/echo_langgraph/handle.py`: `handle(input: dict, ctx: dict) -> AsyncIterator[dict]`. A two-node `StateGraph` (`model`, `tools`, joined by `tools_condition`). The model is `ChatOpenAI` with streaming and `stream_usage` on. Prompt `simplifier-v1`, the same as echo-python: the system prompt is fixed, and the input text is its own human message.
- `src/echo_langgraph/mapping.py`: LangGraph stream parts (`astream(stream_mode=["messages", "updates"])`) to chassis events: `start`, `delta` per text chunk, `tool_call` once the tool result arrives, `metrics` summed from `usage_metadata` over every model call, `end`. A failure is one `error` event: `http_<status>` (retryable for 5xx), `timeout`, `connect_error`, else `model_error`. `engine_error` is the pipeline's code, not the workload's. The file is on its own so its size can be counted.
- `src/echo_langgraph/tools.py`: the chassis's MCP tools (`CHASSIS_TOOL_URL`, default `http://127.0.0.1:8090/mcp`) as LangChain tools, over the `mcp` 2 client. It is a stand-in: the locked `langchain-mcp-adapters` 0.3.1 does not import against `mcp` 2.2.0. The workload defines no tool of its own. An unreachable endpoint means a run with no tools, logged and counted, as in echo-python.
- Model calls go to `CHASSIS_MODEL_URL` (default `http://127.0.0.1:8090/v1`), the chassis's model proxy. `ctx.model_route` is the model; `big-default` when unset. The openai client needs a key string, so it gets the placeholder `not-a-key`. The proxy ignores `Authorization` and the chassis holds the real key. No `*_API_KEY` variable is read. Only in the `remote` lane, when `CHASSIS_API_TOKEN` is set and not empty, that token is the model's `api_key` and an `Authorization: Bearer` header on every MCP request; unset, the placeholder stays and MCP sends none.
- `ctx["traceparent"]`, when set, is a plain `traceparent` header on every model call (`default_headers`) and every MCP request (the MCP client's headers). No OpenTelemetry instrumentation.
- The timeout for each call is `ctx.budget.timeout_ms` (suggested: 30 s when unset). The workload does not retry (`max_retries=0`); the chassis decides.

## Run

```bash
workload-a2a serve --handle echo_langgraph:handle --port 9000
docker build -f packages/workloads/echo-langgraph/Dockerfile -t echo-langgraph .   # from the repo root
```

## Test

`scripts/check_offline.sh packages/workloads/echo-langgraph`. The tests drive `handle` against the fake model server and a FastMCP stub of the tool endpoint, both over `httpx2.ASGITransport`, through the `handle.model_transport` and `tools.transport` hooks. No socket, no key.
