# echo-smolagents

The simplifier as a smolagents `CodeAgent` (`smolagents[mcp]==1.26.0`). PoC-6, part 6b. Trust: `untrusted`. Lane: `remote`.

`handle(input: dict, ctx: dict) -> AsyncIterator[dict]` is the wire form of the contract. The events carry `schema_version: "0"`, in this order: `start`, `tool_call` per MCP call, `delta`, `metrics`, `end`. Or `start` and one `error`. `handle` never raises.

## Shape

- `src/echo_smolagents/handle.py`: `handle`, the worker thread, the headers. `transport` is the test-only model hook, a sync `httpx2.BaseTransport`. The skeleton had an async one. smolagents is synchronous, so its openai client is the sync one.
- `src/echo_smolagents/agent.py`: `ChassisModel` (an `OpenAIServerModel`) and `SimplifierAgent` (a `CodeAgent`).
- `src/echo_smolagents/tools.py`: the chassis MCP tools as smolagents `Tool`s. `transport` is the test-only MCP hook (async `httpx2`).
- `src/echo_smolagents/mapping.py`: smolagents events to chassis events, and the error codes. Its own file so the size can be counted.
- No model key. The model URL and the tool URL are the chassis's proxies (`CHASSIS_MODEL_URL`, `CHASSIS_TOOL_URL`).

## How a run goes

1. `handle` yields `start`, builds the openai client and lists the MCP tools.
2. A worker thread runs `agent.run(text, stream=True)`. smolagents is sync. Events go back through a `queue.Queue`, and `handle` reads it with `asyncio.to_thread`, so the event loop is never blocked.
3. The model replies with Python in `<code>` ... `</code>`. smolagents runs it in this process. When that code calls an MCP tool, the tool makes the call (its own short `asyncio.run` session) and queues one `tool_call{call_id, name, arguments, result}`.
4. The code ends with `final_answer(...)`. That text is the one `delta`. Then `metrics` and `end`.
5. A consumer that stops early sets the agent's interrupt flag, so the run ends at its next step.

Input and instructions: the input text is the task, its own user message. The fixed line "Rewrite in plain words. Short sentences. Keep every fact." goes in the system prompt through smolagents' `instructions`. The input is never merged into the system prompt (025 H-10, tested).

## Design choices

- **Tools: a stand-in, not `ToolCollection.from_mcp`.** `from_mcp` is `mcpadapt` 0.1.20, written for `mcp` 1.x. Against the locked `mcp` 2.2.0, `import mcpadapt.core` fails: `cannot import name 'streamablehttp_client'`. It also has no way to inject headers or take the transport hook. So `tools.py` is a small `Tool` over the `mcp` 2 client, as echo-langgraph does. When an `mcpadapt` for `mcp` 2 is locked, it becomes `MCPClient(...).get_tools()`.
- **Tools return the structured result** (a dict) to the generated code, so it can write `r["definition"]`. An unreachable endpoint at list time means no tools, and the run goes on (`tools.list_failures` counts it).
- **A failed MCP call is fatal.** The call yields a `tool_call` with `{"error": ...}`, then the run ends with `error{code: "tool_error"}`, even if the generated code caught the exception. This matches echo-python. smolagents alone would hand the error to the model and retry.
- **Steps.** `max_steps=4`: three tool rounds and the final answer. Running out is `error{code: "tool_loop_exceeded"}`. smolagents would make one more model call for a forced answer. `SimplifierAgent` overrides `_handle_max_steps_reached` (a private method, safe because the version is pinned) to raise at once.
- **Errors.** `http_<status>` (retryable for 5xx), `timeout` and `connect_error` (retryable), `tool_error`, `tool_loop_exceeded`, `bad_response` (a 200 that is not a chat completion), else `model_error`.
- **Headers.** `ctx.traceparent` is a default header on the model client and on every MCP request. `CHASSIS_API_TOKEN`, when set and not empty, is the bearer on both. Unset, a request hook removes `Authorization`, and the openai client gets a placeholder key. `OPENAI_API_KEY`, `OPENAI_ADMIN_KEY`, and `OPENAI_ORG_ID` are never read for the request.
- **Metrics.** The agent's monitor sums the tokens of every model call.
- **No retries**: `max_retries=0` and `retry=False`. The timeout is `ctx.budget.timeout_ms` (30 s if unset, suggested). It also bounds one run of the generated code.
- **Logging off.** smolagents prints the task and the code to stdout at its default level. `verbosity_level=OFF` stops that.

## Streaming fidelity

The model's intermediate code, its thoughts, and the observations are not streamed to the user. The one `delta` is the final answer, sent at the end. The user sees nothing until the run is done. This is a bake-off criterion: a `CodeAgent` has no natural token stream of the answer, because the answer is a string inside a `final_answer(...)` call.

## Why it is `untrusted` and `remote`

The `CodeAgent` asks the model for Python and runs it inside the workload process. The model's text decides what runs. The tool results and the input are data an attacker may shape, so a prompt injection can steer the code.

What smolagents' `LocalPythonExecutor` allows (checked in `tests/test_echo_smolagents_errors.py`):

- Imports: only `collections, datetime, itertools, math, queue, random, re, stat, statistics, time, unicodedata`. We add none. `import os`, `import subprocess`, and `from os import path` fail with `Import of ... is not allowed`.
- `open`, `__import__`, `eval`, `exec`, and dunder attribute walks (`[].__class__.__base__`) fail. A module reached through an allowed one (`random._os`, `queue.threading`) is refused.
- Tools: `final_answer` and the MCP tools, called as functions.
- A run of generated code is cut after `ctx.budget.timeout_ms`.

That is an AST interpreter with deny lists, not a security boundary. Its own docs say so, and escapes are found now and then. The in-process executor is the reason the lane is `remote`: the real boundary is the gVisor sandbox pod, no key in it, egress only to the chassis. The process holds no provider key. It does hold its per-remote bearer token (`CHASSIS_API_TOKEN`), and model-written code can read it. That token works only from inside this pod: the NetworkPolicy lets only the pod's own chassis in, and the chassis remote proxy also needs the `traceparent` of a run in flight. The chassis scrubs that exact token from every event it reads back (contract v5). So the worst code can reach is the chassis proxies and what the pod's network allows.

What it writes to disk: nothing. A test runs the lookup and a refused `import os` with `cwd`, `HOME`, and the temp dir empty, and checks they stay empty. The executor keeps state in memory only.

Writable paths for the Dockerfile: none are required by the code. `HOME` is `/tmp` (the image sets it), and the sandbox pod mounts an emptyDir there as for every remote. The root filesystem and `/app` can be read-only.

## Router compatibility

Every key of the request body this workload sends to `POST /v1/chat/completions`:

| Key | Value |
| --- | ----- |
| `model` | `ctx.model_route` (default `big-default`) |
| `messages` | system prompt, the task as a user message, then the code and observations of earlier steps. Content is a list of `{"type": "text", "text": ...}` parts |
| `stop` | `["Observation:", "Calling tools:", "</code>"]` |

That is all. No `stream`, `tools`, `temperature`, `max_tokens`, or `tool_choice` (the `CodeAgent` does not use OpenAI tool calls). A test asserts the exact key set.

The chassis model proxy keeps only `model`, `messages`, `temperature`, `max_tokens`, `tools`, and `stream`. So `stop` is dropped. Does that break the engine? No, because `ChassisModel` always cuts the reply at the stop sequences itself, with smolagents' own `remove_content_after_stop_sequences`. Without that, a model that runs past `</code>` writes a made-up `Observation:` and a second code block, and smolagents joins every block it finds and runs them as one. A test shows both: with the cut, the `overrun` reply gives the first block's answer. Without the cut, `parse_code_blobs` returns both blocks (the second would give the wrong answer). Another test runs the whole lookup task through a filter that keeps only the six keys, with the fake model, and it passes.

The cost of dropping `stop`: the model may generate tokens past `</code>` that are billed and then discarded. Those tokens also count in `metrics`. A model that supports `stop` stops early when the proxy is changed to forward it. One more rule: for models whose id matches smolagents' no-`stop` list (`o3`, `o4`, `gpt-5*`, `grok-*`), smolagents does not send `stop` at all, and the cut does all the work. The route is `big-default`, so the id never matches here.

## Hidden state between runs

- A new `CodeAgent`, executor, and monitor per run, so no memory, variables, or token counts carry over (tested: the second run's request holds nothing from the first).
- Module globals: `tools.list_failures` (a counter) and the two test hooks. Nothing else.
- smolagents has no telemetry and no disk cache of its own. Its logger writes to stdout unless it is off (it is).
- The worker thread is a daemon. After an early stop it finishes its current step.

## Instrumentation

The OpenInference instrumentor is `openinference-instrumentation-smolagents` (`SmolagentsInstrumentor().instrument()`). It comes with smolagents' `telemetry` extra. It is not installed here and not in `uv.lock`, so the workload emits no spans today. It wraps the agent's `run`, each step, the model call, and each tool call, and exports over OTLP. PoC-6 does not enable it. `observability-expert` decides whether to add it, behind a port, with the traceparent from `ctx`.

## License and maturity

- smolagents 1.26.0: Apache-2.0 (Hugging Face). It is 1.x and moves fast. The 1.x line changed the default code tags (`<code>`), the memory classes, and `OpenAIServerModel` (now `OpenAIModel`) between minors. The version is pinned. Two private pieces are used: `_handle_max_steps_reached` and `_prepare_completion_kwargs`.
- `mcpadapt` 0.1.20 (MIT) is installed by the `mcp` extra but not usable with `mcp` 2 (see above).

## Run

```bash
uv run workload-a2a serve --handle echo_smolagents:handle --port 9000
docker build -f packages/workloads/echo-smolagents/Dockerfile -t echo-smolagents .   # from the repo root
```

## Test

```bash
scripts/check_offline.sh packages/workloads/echo-smolagents -q
```

The model is the fake model server on `tests/scripts/smolagents.yaml`, and the tools are a FastMCP stub, both served on Unix sockets in the test (the only sockets allowed offline). The script is package-local because a `CodeAgent` needs code-format replies, and the tool results come back as the next user message, not as a `tool` message.

- `test_echo_smolagents_tasks.py`: smoke, simplifier, and lookup from the task spec, event schema, metrics, input as its own message.
- `test_echo_smolagents_wire.py`: `traceparent` and bearer on every model and MCP request, the planted-key check, the body keys, the router filter.
- `test_echo_smolagents_errors.py`: the error codes, the step limit, the executor limits, no disk writes, no shared state.
