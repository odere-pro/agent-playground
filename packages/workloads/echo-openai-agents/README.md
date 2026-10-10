# echo-openai-agents

The simplifier as an OpenAI Agents SDK agent (`openai-agents==0.23.1`), in the `sidecar` lane (trust: `trusted`). PoC-6, part 6a. It is a workload: `handle(input, ctx)` in the wire form, served over A2A by `workload-a2a`, with no chassis import and no key.

Same prompt (`simplifier-v1`), same event order, and same error codes as `echo-python` and `echo-pydanticai`. The input text is its own user message; the prompt is the SDK's `instructions`, so it is the system message.

## Shape

- `src/echo_openai_agents/handle.py`: `handle`. Builds one `AsyncOpenAI` client, one MCP server, and one `Agent` per run. `transport` is the test-only model hook (`httpx2`).
- `src/echo_openai_agents/mapping.py`: SDK stream events to chassis events, and `error_event`. Its own file, so its size can be counted (165 lines with the error mapping; `echo-pydanticai`: 133).
- `src/echo_openai_agents/tools.py`: the MCP server class and the test-only MCP hook `transport` (`httpx2`).
- Env: `CHASSIS_MODEL_URL` (default `http://127.0.0.1:8090/v1`), `CHASSIS_TOOL_URL` (default `http://127.0.0.1:8090/mcp`), `CHASSIS_API_TOKEN` (remote lane only). No `*_API_KEY` variable is read.

## The SDK APIs used

| API | Used for |
| --- | -------- |
| `agents.Agent(name, instructions, model, mcp_servers)` | The agent. `instructions` is the simplifier prompt. |
| `agents.OpenAIChatCompletionsModel(model=route, openai_client=...)` | The chat completions model. The SDK defaults to the Responses API; this class does not use it. |
| `openai.AsyncOpenAI(base_url, api_key, http_client, max_retries=0, timeout)` | The client at the chassis model proxy. The `httpx2` client built on the `handle.transport` hook carries the headers. |
| `agents.Runner.run_streamed(agent, text, max_turns, hooks, run_config)` | The streamed run. `max_turns` is 4: three tool rounds plus the answer. |
| `RunResultStreaming.stream_events()` | The events the mapping reads. `cancel()` runs in a `finally`. |
| `RunResultStreaming.context_wrapper.usage` | Input and output tokens summed over every model call. |
| `agents.RunHooks.on_llm_end` | Counts the model responses that ask for tools. The 4th raises before the SDK runs that round's tools. |
| `agents.mcp.MCPServerStreamableHttp(params, ...)` | The chassis MCP tools. The `httpx_client_factory` param builds the client on the `tools.transport` hook. `use_structured_content=True` makes a result one JSON object. `failure_error_function=None` makes a failed tool raise. |
| `agents.set_tracing_disabled`, `agents.tracing.set_trace_processors`, `RunConfig(tracing_disabled=True)` | Tracing off (below). |

The MCP server class is a subclass: `list_tools` returns `[]` and counts `tools.list_failures` when the endpoint is unreachable (the run goes on with no tools, with a warning), and `call_tool` raises `ToolFailed` on a transport error or an `isError` result (`tool_error`).

## Mapping (`mapping.py`)

| SDK event | Chassis event |
| --------- | ------------- |
| `RawResponsesStreamEvent` with `response.output_text.delta` | `delta {text}` |
| `RunItemStreamEvent` `tool_called` | held, no event |
| `RunItemStreamEvent` `tool_output` | one `tool_call {call_id, name, arguments, result}` (the held call plus the output; the output is the MCP structured content parsed to an object, else `{"text": ...}`) |
| end of `stream_events()` | `metrics {input_tokens, output_tokens, model_route, attempt: 1}`, then `end {status: ok}` |
| every other raw or item event | nothing |
| an exception | one `error` |

Error codes:

| Cause | Code | Retryable |
| ----- | ---- | --------- |
| `openai.APIStatusError` | `http_<status>` | 5xx only |
| `ToolLoopExceeded` (our hook), `MaxTurnsExceeded` | `tool_loop_exceeded` | no |
| `ToolFailed` | `tool_error` | no |
| httpx timeout, `openai.APITimeoutError` | `timeout` | yes |
| httpx transport error, `openai.APIConnectionError` | `connect_error` | yes |
| `ModelBehaviorError`, `APIResponseValidationError`, `JSONDecodeError` | `bad_response` | no |
| anything else | `model_error` | no |

## Tracing is off

The SDK exports traces to `api.openai.com` through a default `BatchTraceProcessor`. At import, `handle.py` calls `set_trace_processors([])` and `set_tracing_disabled(True)`, and each run passes `RunConfig(tracing_disabled=True)`. `test_the_sdk_trace_export_is_off` adds a spy processor and patches the batch processor and the exporter: none sees a trace, a span, or an export. Both switches are process-wide (fine for a workload that is its own process). A deployment that wants SDK traces would add its own processor behind the chassis, not OpenAI's.

## Router compatibility

The chassis proxy keeps `model`, `messages`, `temperature`, `max_tokens`, `tools`, and `stream`, and ignores other keys. The SDK is checked in two ways (`tests/test_oai_router_compat.py`, which records the request bodies at the fake model server).

**What `handle` sends**, on every model call, to `POST /v1/chat/completions` (the only path; no Responses API call):

| Key | Kept by the proxy |
| --- | ----------------- |
| `model` | yes (the route) |
| `messages` | yes (`system`, `user`, `assistant` with `tool_calls` and `content: null`, `tool` with `tool_call_id`; no other key) |
| `tools` | yes (each function has `strict: false` besides `name`, `description`, `parameters`; the proxy ignores `strict`) |
| `stream` | yes (`true`) |

So nothing `handle` sends is dropped. Not sent, so the proxy's own defaults apply: `temperature` (proxy default 0.0), `max_tokens` (the run's remaining budget), `tool_choice`, `parallel_tool_calls`. `stream_options` is not sent either, because the SDK adds it only for a base URL that is OpenAI's own; the proxy puts `usage` on the last chunk anyway, and the SDK reads it from there (the metrics test sums three calls).

**What the SDK can send** when a `ModelSettings` field is set (the workload sets none). With every field set at once the body has these keys; the ones the proxy drops are marked:

`model`, `messages`, `tools`, `stream`, `temperature`, `max_tokens` (kept); `top_p`, `frequency_penalty`, `presence_penalty`, `tool_choice`, `parallel_tool_calls`, `stream_options`, `store`, `reasoning_effort`, `verbosity`, `top_logprobs`, `logprobs`, `metadata` (dropped). `response_format` (when the agent has an `output_type`), `prompt_cache_retention`, and `prompt_cache_options` are the other keys the SDK code has; they are not sent unless set.

Does it matter? Not for this workload: it sets none of the dropped keys. It would matter if a later agent set `tool_choice="required"` or a named tool (the proxy would ignore it, silently, and the model could answer without a tool), `parallel_tool_calls=False` (ignored, the model may still return several calls; the SDK handles that), `top_p` or penalties (ignored, no error), `response_format` (an `output_type` with structured output; ignored too, so the reply may not be JSON), or `reasoning_effort` (ignored). The SDK does not fail when a key is ignored. The proxy refuses a message key it cannot carry with a value (400 `unsupported_message`); the SDK sends none (checked by `test_message_and_tool_keys_are_ones_the_port_carries`).

## Hidden state

None kept between runs by the workload. The SDK has global state, and the workload touches only the tracing part:

- Tracing: the global trace provider and its processors (above). Changed at import, on purpose.
- `agents.models._openai_shared`: a default OpenAI key and client, and `use_responses_by_default`. Not used: the model is passed an explicit `AsyncOpenAI` client and `OpenAIChatCompletionsModel`, so no default client reads `OPENAI_API_KEY` (the planted key test).
- No `Session`, so no conversation memory. `test_two_runs_share_no_state` runs the same input twice and checks the next run's model call holds only its own two messages.
- One MCP server and one HTTP client per run, closed in a `finally`. The tool list is not cached (`cache_tools_list=False`).
- `tools.list_failures` is a counter of this workload, not the SDK's.

## OpenInference and Temporal (bake-off criteria)

Neither is installed in the workspace, and the network was not used, so these two are from the SDK source and from memory, not run:

- **OpenInference.** The SDK has its own tracing (spans for generations, functions, and MCP tools) behind the processor interface (`TracingProcessor`). The OpenInference instrumentor for it (`openinference-instrumentation-openai-agents`, from Arize) registers such a processor and emits OpenInference spans, so Langfuse or Phoenix can read them. Here that is off by design: the chassis owns the spans (`chassis.model.call`, `traceparent`), and a workload span would need a trace context joined to `ctx.traceparent`. Not tried; to verify in PoC-6 scoring.
- **Temporal.** The SDK source has hooks for it (`run_internal/agent_runner_helpers.py` imports `temporalio.workflow` and checks `in_workflow()`), and Temporal's Python SDK ships an Agents SDK integration (`temporalio.contrib.openai_agents`, from memory). `temporalio` is not installed, so it is unverified. A durable run would put the model calls in activities, which clashes with the chassis owning the model call. Not a PoC-6 goal.

## Run

```bash
uv run workload-a2a serve --handle echo_openai_agents:handle --port 9000
docker build -f packages/workloads/echo-openai-agents/Dockerfile -t echo-openai-agents .   # from the repo root
```

## Test

```bash
scripts/check_offline.sh packages/workloads/echo-openai-agents -q
```

- `tests/test_oai_tasks.py`: smoke, simplifier, and lookup against the task spec; every event against `events.v0.json`; `traceparent` and the remote token on every model and MCP request; the planted `OPENAI_API_KEY`; the metrics sum; no shared state.
- `tests/test_oai_errors.py`: each error code, the 4th-round limit, tracing off.
- `tests/test_oai_router_compat.py`: the request body keys.
- `tests/oai_support.py`: the fake model server on `scripts/bakeoff.yaml` and a two-tool stub (`glossary_lookup`, `acronym_expand`) built from `fake_mcp_server`, both over `httpx2.ASGITransport`. No socket, no key.
