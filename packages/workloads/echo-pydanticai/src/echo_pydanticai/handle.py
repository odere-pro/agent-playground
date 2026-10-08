"""The simplifier as a PydanticAI `Agent`, streamed back as chassis events.

`handle(input, ctx)` is the wire form of the contract (docs/contracts/contract-v0.md): plain dicts
in, event dicts out, `schema_version: "0"` on each. Same prompt, same event sequence, and same
error codes as echo-python; the framework mapping is in `mapping.py`.

- Model: an OpenAI-compatible chat model at `CHASSIS_MODEL_URL`, the chassis's model proxy. The
  route is `ctx["model_route"]`.
- Tools: the chassis's MCP endpoint at `CHASSIS_TOOL_URL`, as a PydanticAI `MCPToolset`. The model
  is offered what the chassis serves (`glossary_lookup`); this workload defines no tool.
- `ctx["traceparent"]`, when set, is a default header on both HTTP clients, so every model call
  and every MCP request carries it (contract v1 decision 4). A plain header, no OpenTelemetry.
- No model key. In the `remote` lane `CHASSIS_API_TOKEN`, when set and not empty, is the openai
  client's `api_key` (so `Authorization: Bearer <token>` on every model call) and a default header
  on the MCP client; unset, no `Authorization` header is sent. Read once per run, never logged.
- The input text is its own user message, never merged into the system prompt.
- Timeouts: `ctx.budget.timeout_ms` is the timeout of each model call and each MCP request (the
  HTTP clients and the MCP toolset). There is no whole-run deadline here: one around the `yield`s
  would fire while the generator is suspended and cancel the consumer (the template server's
  `execute`) instead of ending in a `timeout` event. The connector owns the run deadline.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from echo_pydanticai.mapping import EventMapper, error_event, event

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
# The openai SDK refuses an empty key, so it gets this placeholder, never an `*_API_KEY` variable.
# It is not sent either: `_drop_authorization` removes the header. The chassis model proxy ignores
# `Authorization` anyway and adds the real, scoped key itself; only the chassis holds credentials.
PLACEHOLDER_API_KEY = "not-a-key"

model_transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook: when set, model calls go through this `httpx2` transport, not a socket."""
tool_transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook: when set, MCP requests go through this `httpx2` transport, not a socket."""


def timeout_s(ctx: dict[str, Any]) -> float:
    budget = ctx.get("budget")
    if isinstance(budget, dict) and budget.get("timeout_ms"):
        return float(budget["timeout_ms"]) / 1000
    return DEFAULT_TIMEOUT_S


async def _drop_authorization(request: httpx2.Request) -> None:
    request.headers.pop("authorization", None)


def _client(
    transport: httpx2.AsyncBaseTransport | None,
    headers: dict[str, str],
    timeout: float,
    *,
    token: str | None,
) -> httpx2.AsyncClient:
    """With a remote token the `Authorization` header stays; without one it is dropped."""
    return httpx2.AsyncClient(
        transport=transport,
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
        event_hooks={} if token else {"request": [_drop_authorization]},
    )


def _agent(
    route: str, model_http: httpx2.AsyncClient, tools: MCPToolset[None], token: str | None
) -> Agent[None]:
    openai = AsyncOpenAI(
        base_url=os.environ.get(MODEL_URL_VAR, DEFAULT_MODEL_URL).rstrip("/"),
        api_key=token or PLACEHOLDER_API_KEY,
        admin_api_key="",  # never fall back to OPENAI_ADMIN_KEY
        http_client=model_http,
        max_retries=0,  # the chassis owns retries and fallback
    )
    model = OpenAIChatModel(route, provider=OpenAIProvider(openai_client=openai))
    return Agent(model, system_prompt=SYSTEM_PROMPT, toolsets=[tools])


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
            model_http = _client(model_transport, headers, timeout, token=token)
            stack.push_async_callback(model_http.aclose)
            # Not entered here: the MCP transport opens this client itself, and a client opens once.
            tool_headers = {**headers, "authorization": f"Bearer {token}"} if token else headers
            tool_http = _client(tool_transport, tool_headers, timeout, token=token)
            stack.push_async_callback(tool_http.aclose)
            tools: MCPToolset[None] = MCPToolset(
                os.environ.get(TOOL_URL_VAR, DEFAULT_TOOL_URL),
                http_client=tool_http,
                init_timeout=timeout,
                read_timeout=timeout,
            )
            agent = _agent(route, model_http, tools, token)
            events = await stack.enter_async_context(agent.run_stream_events(text))
            async for ev in events:
                for out in mapper.map(ev):
                    yield out
    except Exception as exc:
        yield error_event(exc)
