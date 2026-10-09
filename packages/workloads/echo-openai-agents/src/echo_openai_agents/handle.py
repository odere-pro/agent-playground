"""The simplifier as an OpenAI Agents SDK `Agent`, streamed back as chassis events.

`handle(input, ctx)` is the wire form of the contract (docs/contracts/contract-v0.md): plain dicts
in, event dicts out, `schema_version: "0"` on each. Same prompt, same event sequence, and same
error codes as echo-python and echo-pydanticai; the framework mapping is in `mapping.py`.

- Model: `OpenAIChatCompletionsModel` (chat completions, not the SDK's default Responses API) on
  an `AsyncOpenAI` client at `CHASSIS_MODEL_URL`, the chassis's model proxy. The route is
  `ctx["model_route"]`. The client has `max_retries=0`: the chassis owns retries and fallback.
- Tools: the chassis's MCP endpoint at `CHASSIS_TOOL_URL`, through the SDK's streamable-HTTP MCP
  server (`tools.py`). This workload defines no tool. An unreachable endpoint is a run with no
  tools.
- Tracing: the SDK exports traces to OpenAI by default. Both switches are off here: no trace
  processor is registered (`set_trace_processors([])`), and `tracing_disabled` is set globally and
  on each run's `RunConfig`. Nothing is ever sent to OpenAI.
- `ctx["traceparent"]`, when set, is a default header on both HTTP clients, so every model call
  and every MCP request carries it (contract v1 decision 4). A plain header, no OpenTelemetry.
- No model key. In the `remote` lane `CHASSIS_API_TOKEN`, when set and not empty, is the bearer on
  every model call and MCP request. Unset, the client gets a placeholder key and the
  `Authorization` header is dropped before each request leaves. No `*_API_KEY` variable is read.
- The input text is its own user message, never merged into the system prompt (the SDK puts
  `instructions` in the system message).
- The tool loop: at most `MAX_TOOL_ROUNDS` rounds. The SDK's `max_turns` counts model calls, so it
  is rounds + 1 (the last call is the answer). A hook stops the 4th round before its tools run.
- Timeouts: `ctx.budget.timeout_ms` is the timeout of each model call and each MCP request. There
  is no whole-run deadline here: one around the `yield`s would fire while the generator is
  suspended and cancel the consumer. The connector owns the run deadline.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from agents import (
    Agent,
    ModelResponse,
    OpenAIChatCompletionsModel,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    Runner,
    set_tracing_disabled,
)
from agents.mcp import MCPServer
from agents.tracing import set_trace_processors
from openai import AsyncOpenAI

from echo_openai_agents import tools
from echo_openai_agents.mapping import EventMapper, ToolLoopExceeded, error_event, event

log = logging.getLogger(__name__)

PROMPT_VERSION = "simplifier-v1"
SYSTEM_PROMPT = "Rewrite in plain words. Short sentences. Keep every fact."
DEFAULT_ROUTE = "big-default"
MODEL_URL_VAR = "CHASSIS_MODEL_URL"
DEFAULT_MODEL_URL = "http://127.0.0.1:8090/v1"
TOKEN_VAR = "CHASSIS_API_TOKEN"
TOOL_URL_VAR = "CHASSIS_TOOL_URL"
DEFAULT_TOOL_URL = "http://127.0.0.1:8090/mcp"
# suggested: 30 s per call when `ctx.budget.timeout_ms` is not set; the epic gives none.
DEFAULT_TIMEOUT_S = 30.0
# suggested: 3 tool rounds, the same cap as echo-python.
MAX_TOOL_ROUNDS = 3
# The openai SDK refuses an empty key, so it gets this placeholder, never an `*_API_KEY` variable.
# It is not sent either: `_drop_authorization` removes the header. The chassis model proxy ignores
# `Authorization` anyway and adds the real, scoped key itself; only the chassis holds credentials.
PLACEHOLDER_API_KEY = "not-a-key"  # pragma: allowlist secret (a placeholder, not a key)

# The SDK exports traces to api.openai.com through a default processor. Remove it and switch
# tracing off, at import, so nothing is ever queued or sent (a test proves it).
set_trace_processors([])
set_tracing_disabled(True)

transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook: when set, model calls go through this `httpx2` transport, not a socket. The
MCP hook is `echo_openai_agents.tools.transport`."""


def timeout_s(ctx: dict[str, Any]) -> float:
    budget = ctx.get("budget")
    if isinstance(budget, dict) and budget.get("timeout_ms"):
        return float(budget["timeout_ms"]) / 1000
    return DEFAULT_TIMEOUT_S


async def _drop_authorization(request: httpx2.Request) -> None:
    request.headers.pop("authorization", None)


def _model_client(headers: dict[str, str], timeout: float, token: str | None) -> httpx2.AsyncClient:
    """With a remote token the `Authorization` header stays; without one it is dropped."""
    return httpx2.AsyncClient(
        transport=transport,
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        event_hooks={} if token else {"request": [_drop_authorization]},
    )


class _RoundLimit(RunHooks[Any]):
    """Stops the run when a model response asks for more than `MAX_TOOL_ROUNDS` tool rounds, before
    the SDK runs that round's tools."""

    def __init__(self) -> None:
        self.rounds = 0

    async def on_llm_end(
        self, context: RunContextWrapper[Any], agent: Agent[Any], response: ModelResponse
    ) -> None:
        if any(getattr(item, "type", None) == "function_call" for item in response.output):
            self.rounds += 1
            if self.rounds > MAX_TOOL_ROUNDS:
                raise ToolLoopExceeded(f"more than {MAX_TOOL_ROUNDS} tool rounds")


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """`start`, `delta` per text chunk, `tool_call` per finished call, `metrics`, `end`; or
    `start` then one `error`. Never raises for a model, tool, or transport failure.
    """
    yield event("start", request_id=str(ctx.get("request_id", "")))
    route = str(ctx.get("model_route") or DEFAULT_ROUTE)
    text = str(input.get("text") or "")
    timeout = timeout_s(ctx)
    traceparent = ctx.get("traceparent")
    headers = {"traceparent": str(traceparent)} if traceparent else {}
    token = os.environ.get(TOKEN_VAR) or None
    mapper = EventMapper(route)
    try:
        async with AsyncExitStack() as stack:
            model_http = _model_client(headers, timeout, token)
            stack.push_async_callback(model_http.aclose)
            client = AsyncOpenAI(
                base_url=os.environ.get(MODEL_URL_VAR, DEFAULT_MODEL_URL).rstrip("/"),
                api_key=token or PLACEHOLDER_API_KEY,
                admin_api_key="",  # never fall back to OPENAI_ADMIN_KEY
                http_client=model_http,
                max_retries=0,  # the chassis owns retries and fallback
                timeout=timeout,
            )
            tool_headers = {**headers, "authorization": f"Bearer {token}"} if token else headers
            server = tools.build_server(
                os.environ.get(TOOL_URL_VAR, DEFAULT_TOOL_URL), tool_headers, timeout, token=token
            )
            mcp_servers: list[MCPServer] = []
            try:
                await server.connect()  # type: ignore[no-untyped-call]
            except Exception as exc:  # unreachable: the run goes on with no tools
                tools.count_list_failure(exc)
            else:
                stack.push_async_callback(server.cleanup)
                mcp_servers.append(server)
            agent = Agent(
                name="simplifier",
                instructions=SYSTEM_PROMPT,
                model=OpenAIChatCompletionsModel(model=route, openai_client=client),
                mcp_servers=mcp_servers,
            )
            result = Runner.run_streamed(
                agent,
                text,
                max_turns=MAX_TOOL_ROUNDS + 1,
                hooks=_RoundLimit(),
                run_config=RunConfig(tracing_disabled=True),
            )
            try:
                async for ev in result.stream_events():
                    for out in mapper.map(ev):
                        yield out
            finally:
                result.cancel()
            for out in mapper.finish(result.context_wrapper.usage):
                yield out
    except Exception as exc:
        yield error_event(exc)
