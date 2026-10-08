# echo-pydanticai

The simplifier as a PydanticAI agent (PoC-2). Same prompt (`simplifier-v1`), same event sequence, and same error codes as `echo-python`; only the framework differs. It runs in its own container and is served over A2A by `workload-a2a`.

## Shape

- `src/echo_pydanticai/handle.py`: `handle(input: dict, ctx: dict) -> AsyncIterator[dict]`, the wire form. A PydanticAI `Agent` with the simplifier system prompt, an `OpenAIChatModel` pointed at `CHASSIS_MODEL_URL` (default `http://127.0.0.1:8090/v1`), and an `MCPToolset` on `CHASSIS_TOOL_URL` (default `http://127.0.0.1:8090/mcp`). The route is `ctx.model_route` (`big-default` when unset). The timeout is `ctx.budget.timeout_ms` (suggested: 30 s when unset), for each HTTP call and for the whole run.
- `src/echo_pydanticai/mapping.py`: the event mapping, PydanticAI stream events to chassis events. Kept in its own file so its size can be counted.
- Tools: the chassis serves `glossary_lookup` over MCP; this workload defines no tool of its own.
- Trace: `ctx.traceparent`, when set, is a default header on the model client and the MCP client, so every model call and every MCP request carries it. A plain header; no OpenTelemetry instrumentation.
- No model key. The openai SDK gets the placeholder `not-a-key`, never an `*_API_KEY` variable, and the `Authorization` header is dropped before each request. The chassis proxy adds the real key. Only in the `remote` lane, when `CHASSIS_API_TOKEN` is set and not empty, that token is the openai `api_key` and a default header on the MCP client (`Authorization: Bearer <token>` on every call, header not dropped); unset, none is sent.
- The workload does not retry (`max_retries=0`); the chassis owns retries and fallback.

## PydanticAI APIs used

`Agent(model, system_prompt=..., toolsets=[...])`, `Agent.run_stream_events`, `OpenAIChatModel`, `OpenAIProvider(openai_client=AsyncOpenAI(...))`, `pydantic_ai.mcp.MCPToolset(url, http_client=...)`. Events: `PartStartEvent`, `PartDeltaEvent`, `TextPart`, `TextPartDelta`, `FunctionToolCallEvent`, `FunctionToolResultEvent`, `ToolReturnPart`, `AgentRunResultEvent` (`result.usage`). Errors: `ModelHTTPError`, `UnexpectedModelBehavior`. The openai SDK and the MCP client both run on `httpx2`.

## Run

```bash
CHASSIS_MODEL_URL=http://127.0.0.1:8090/v1 CHASSIS_TOOL_URL=http://127.0.0.1:8090/mcp \
  uv run workload-a2a serve --handle echo_pydanticai:handle --port 9000
docker build -f packages/workloads/echo-pydanticai/Dockerfile -t echo-pydanticai .   # from the repo root
```

The chassis points at it with `spec.engine.connector: sidecar` and `spec.engine.url: http://127.0.0.1:9000`.

## Test

```bash
scripts/check_offline.sh packages/workloads/echo-pydanticai -q
```

`tests/test_pydanticai_handle.py` calls `handle` directly against the fake model server and a FastMCP stub of the tool endpoint, both over `httpx2.ASGITransport` through the module's `model_transport` and `tool_transport` hooks. No socket, no key. It checks the deltas, the tool loop, the metrics, the error codes, the `traceparent` on every request, that a planted `OPENAI_API_KEY` never leaves the process, and the events against `packages/chassis/schemas/events.v0.json`.
