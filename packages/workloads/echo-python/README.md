# echo-python

The plain-Python simplifier: the first workload behind the chassis (PoC-1), and the baseline the PoC-2 frameworks are measured against. `handle(input, ctx)` takes the input text, asks a model to rewrite it in plain words, calls the chassis's tools over MCP when the model asks for them, and streams the answer back as chassis events.

## Shape

- `src/echo_python/handle.py`: `handle(input: dict, ctx: dict) -> AsyncIterator[dict]`. Events follow `packages/chassis/schemas/events.v0.json`: `start`, a `tool_call` per tool the model asked for, `delta` per chunk, one `metrics` summing every model call, `end`; `error` on an HTTP, connection, or tool failure.
- `src/echo_python/tools.py`: the MCP client and the OpenAI tool-loop messages, by hand. It lists the chassis's tools, calls one, and builds the assistant and `tool` messages. No tool schema lives here.
- The model call is OpenAI-compatible HTTP (`POST {CHASSIS_MODEL_URL}/chat/completions`, `stream: true`). `ctx.model_route` is the `model`; `big-default` when unset.
- Prompt `simplifier-v1`: the system prompt is fixed; the input text is its own user message.
- No model key here. The chassis holds the credential. Only in the `remote` lane, when `CHASSIS_API_TOKEN` is set and not empty, every model and MCP call carries `Authorization: Bearer <token>` (the per-remote token); unset (`sidecar` lane) no `Authorization` header is sent.
- Dependencies: `httpx` for the model call, `mcp` for the tools (it brings `httpx2`, the client the MCP SDK takes). Never `chassis`.

## Environment

| Variable | Default | What |
| -------- | ------- | ---- |
| `CHASSIS_MODEL_URL` | `http://127.0.0.1:8090/v1` | The chassis's model proxy, on its localhost-only listener (suggested: port 8090) |
| `CHASSIS_TOOL_URL` | `http://127.0.0.1:8090/mcp` | The chassis's MCP tool endpoint, streamable HTTP, stateless, on the same listener |

## Trace

`ctx["traceparent"]` (contract v1, decision 4), when set, is sent as is as the `traceparent` header on every model call and every MCP request (`initialize`, `tools/list`, `tools/call`). When it is not set, no header is sent.

## The tool loop

1. At the start of a run, list the chassis's tools over MCP and offer them to the model as OpenAI `tools` (`name`, `description`, and the tool's own JSON Schema as `parameters`). If the endpoint cannot be reached, the run goes on without tools; `echo_python.tools.list_failures` counts it and a warning is logged.
2. Stream the model's answer as `delta` events. Streamed `tool_calls` pieces are joined by `index`.
3. If the answer asked for tools: call each over MCP (`tools/call`), yield one `tool_call {call_id, name, arguments, result}` per call, append the assistant message (`content: null`, `tool_calls` with `arguments` as a JSON string) and one `tool` message per result (`tool_call_id`, the result as JSON), and call the model again.
4. At most `MAX_TOOL_ROUNDS = 3` rounds of tool calls (suggested). A 4th ask is `error {code: "tool_loop_exceeded", retryable: false}`.

A failed `tools/call` (transport failure or an `isError` result) is `error {code: "tool_error", retryable: false}`. Arguments that are not a JSON object are `error {code: "bad_response"}`. A result that is not an object becomes `{"text": ...}`, because an event's `result` is an object. Each MCP operation opens its own short session (the endpoint is stateless), so no MCP task group stays open while `handle` yields.

## Run

The chassis loads it: `spec.engine.connector: inprocess`, `spec.engine.handle: echo_python:handle` (`packages/chassis/configs/fake.yaml`).

## Test

`uv run pytest packages/workloads/echo-python`. The tests drive `handle` against the fake model server over an ASGI transport (the `echo_python.handle.transport` hook), and against a FastMCP stub on `httpx2.ASGITransport` (the `echo_python.tools.client_factory` hook). No socket, no key. By default the tests make the tool endpoint refuse the connection, so a test without the stub runs with no tools.
