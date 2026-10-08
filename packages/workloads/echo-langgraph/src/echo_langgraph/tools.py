"""The chassis's MCP tools as LangChain tools: a stand-in for `langchain-mcp-adapters`.

Why it exists: the locked `langchain-mcp-adapters` 0.3.1 is written for `mcp` 1.x and does not
import against the locked `mcp` 2.2.0 (`mcp.shared.session`, `mcp.shared.context.RequestContext`
and `mcp.server.fastmcp` are gone, and `streamable_http_client` yields two streams, not three).
When a release for `mcp` 2 is locked, `load_tools` becomes `MultiServerMCPClient(...).get_tools()`
and this file goes away; the tool node and the event mapping stay as they are.

The tool is the chassis's: its name, description, and JSON Schema come from `tools/list`, and
nothing here defines one. Each MCP operation opens its own short session over streamable HTTP
(the chassis endpoint is stateless), with the run's headers (`traceparent`) on every request.
Tool descriptions and results are data, never instructions.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

TOOL_URL_VAR = "CHASSIS_TOOL_URL"
DEFAULT_TOOL_URL = "http://127.0.0.1:8090/mcp"
log = logging.getLogger(__name__)

transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook. When set, MCP calls go through this transport (for example an
`httpx2.ASGITransport` on a FastMCP app) instead of a socket."""

list_failures = 0
"""How many runs found the tool endpoint unreachable and ran without tools (also logged)."""


@asynccontextmanager
async def _session(headers: dict[str, str], timeout_s: float) -> AsyncIterator[ClientSession]:
    url = os.environ.get(TOOL_URL_VAR, DEFAULT_TOOL_URL)
    async with (
        httpx2.AsyncClient(headers=headers, timeout=timeout_s, transport=transport) as http,
        streamable_http_client(url, http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def _tool(
    name: str, description: str, schema: dict[str, Any], headers: dict[str, str], timeout_s: float
) -> BaseTool:
    async def call(**arguments: Any) -> tuple[str, dict[str, Any] | None]:
        async with _session(headers, timeout_s) as session:
            result = await session.call_tool(name, arguments)
        text = "".join(getattr(part, "text", "") for part in getattr(result, "content", []))
        if getattr(result, "is_error", False):
            raise ToolException(text[:200] or "tool error")  # a `ToolMessage` with status error
        structured = getattr(result, "structured_content", None)
        # The artifact shape `langchain-mcp-adapters` uses, so the mapping reads either one.
        return text, {"structured_content": structured} if isinstance(structured, dict) else None

    return StructuredTool.from_function(
        coroutine=call,
        name=name,
        description=description,
        args_schema=schema,
        response_format="content_and_artifact",
        handle_tool_error=True,
    )


async def load_tools(headers: dict[str, str], timeout_s: float) -> list[BaseTool]:
    """The chassis's tools; `[]` when the endpoint cannot be reached, as in echo-python."""
    global list_failures
    try:
        async with _session(headers, timeout_s) as session:
            listed = (await session.list_tools()).tools
    except Exception as exc:  # the SDK wraps transport failures in an ExceptionGroup
        list_failures += 1
        url = os.environ.get(TOOL_URL_VAR, DEFAULT_TOOL_URL)
        log.warning("tool endpoint %s unreachable, running without tools: %r", url, exc)
        return []
    return [_tool(t.name, t.description or "", t.input_schema, headers, timeout_s) for t in listed]
