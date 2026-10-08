"""The simplifier as a LangGraph graph: a model node and a tool node, streamed as chassis events.

`handle(input, ctx)` is the wire form of the contract (docs/contracts/contract-v0.md): plain dicts
in, event dicts out, `schema_version: "0"` on each. The model is `ChatOpenAI` pointed at
`CHASSIS_MODEL_URL`, the chassis's model proxy. The tools come from the chassis's MCP endpoint at
`CHASSIS_TOOL_URL` (`tools.py`, a stand-in for `langchain-mcp-adapters`, which does not import
against the locked `mcp` 2); this workload defines no tool of its own. No key lives here. The
input text is its own human message, never merged into the system prompt.

`traceparent`: `ctx["traceparent"]`, when set, is a plain header on every call (contract v1,
item 4): `default_headers` on the chat model's openai client, and the headers of the httpx2
client every MCP session runs on. No OpenTelemetry instrumentation.

Remote lane: `CHASSIS_API_TOKEN`, when set and not empty, is the chat model's `api_key` (so
`Authorization: Bearer <token>` on every model call) and an `authorization` header on every MCP
request. Unset, the model gets the placeholder key and MCP sends no `Authorization`. The token is
read once per run and never logged.

Both paths use `httpx2`, the HTTP client line of openai 3 and mcp 2, not `httpx` 0.28.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx2
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import SecretStr

from echo_langgraph import tools as mcp_tools
from echo_langgraph.mapping import EventMapper, error_event, event

PROMPT_VERSION = "simplifier-v1"
SYSTEM_PROMPT = "Rewrite in plain words. Short sentences. Keep every fact."
DEFAULT_ROUTE = "big-default"
MODEL_URL_VAR = "CHASSIS_MODEL_URL"
TOKEN_VAR = "CHASSIS_API_TOKEN"
DEFAULT_MODEL_URL = "http://127.0.0.1:8090/v1"
# suggested: 30 s per call when `ctx.budget.timeout_ms` is not set; the epic gives none.
DEFAULT_TIMEOUT_S = 30.0
# suggested: one tool loop is model, tools, model; 10 graph steps leaves room and stops a runaway.
RECURSION_LIMIT = 10
# The openai client refuses an empty key, so it gets this placeholder. It is not a secret: the
# chassis model proxy ignores `Authorization` and adds the real key itself. No `*_API_KEY`
# variable is ever read here; passing `api_key` stops ChatOpenAI reading `OPENAI_API_KEY`.
PLACEHOLDER_KEY = "not-a-key"

model_transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook. When set, model calls go through this transport (for example an
`httpx2.ASGITransport` on the fake model server) instead of a socket. The MCP hook is
`echo_langgraph.tools.transport`."""


def _timeout(ctx: dict[str, Any]) -> float:
    budget = ctx.get("budget")
    if isinstance(budget, dict) and budget.get("timeout_ms"):
        return float(budget["timeout_ms"]) / 1000
    return DEFAULT_TIMEOUT_S


def _messages(text: str) -> list[BaseMessage]:
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=text)]


def _graph(model: ChatOpenAI, tools: list[BaseTool]) -> CompiledStateGraph[Any]:
    """model -> tools -> model until the model stops asking. No tools: the model node alone (an
    empty `tools` list is not sent)."""
    bound = model.bind_tools(tools) if tools else model

    async def call_model(state: MessagesState) -> dict[str, Any]:
        return {"messages": [await bound.ainvoke(state["messages"])]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_edge(START, "model")
    if tools:
        graph.add_node("tools", ToolNode(tools))
        graph.add_conditional_edges("model", tools_condition)
        graph.add_edge("tools", "model")
    return graph.compile()


async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """`start`, `delta` per chunk, `tool_call` per tool result, `metrics`, `end`; or `error`."""
    yield event("start", request_id=str(ctx.get("request_id", "")))
    route = str(ctx.get("model_route") or DEFAULT_ROUTE)
    timeout_s = _timeout(ctx)
    headers = {"traceparent": str(ctx["traceparent"])} if ctx.get("traceparent") else {}
    token = os.environ.get(TOKEN_VAR) or None
    tool_headers = {**headers, "authorization": f"Bearer {token}"} if token else headers
    mapper = EventMapper(route)
    try:
        async with httpx2.AsyncClient(transport=model_transport, timeout=timeout_s) as http:
            tools = await mcp_tools.load_tools(tool_headers, timeout_s)
            model = ChatOpenAI(
                model=route,
                base_url=os.environ.get(MODEL_URL_VAR, DEFAULT_MODEL_URL),
                api_key=SecretStr(token or PLACEHOLDER_KEY),
                http_async_client=http,
                http_socket_options=(),  # our own client; skips a probe socket per construction
                default_headers=headers,
                timeout=timeout_s,
                max_retries=0,  # the chassis owns retries
                streaming=True,
                stream_usage=True,  # sends `stream_options.include_usage`
            )
            config: RunnableConfig = {"recursion_limit": RECURSION_LIMIT}
            state = {"messages": _messages(str(input.get("text") or ""))}
            async for mode, data in _graph(model, tools).astream(
                state, config, stream_mode=["messages", "updates"]
            ):
                for out in mapper.on_part(mode, data):
                    yield out
    except Exception as exc:  # every failure is one `error` event, never a raise out of handle
        yield error_event(exc)
        return
    for out in mapper.finish():
        yield out
