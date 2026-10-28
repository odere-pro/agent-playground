"""The chassis's MCP tools as smolagents `Tool`s: a stand-in for `ToolCollection.from_mcp`.

Why it exists: `ToolCollection.from_mcp` is `mcpadapt` 0.1.20 (the locked version), which is
written for `mcp` 1.x. `import mcpadapt.core` fails against the locked `mcp` 2.2.0 with
`ImportError: cannot import name 'streamablehttp_client'` (it is now `streamable_http_client`, and
takes an `http_client`). So the adapter cannot carry the injected headers or the transport hook
either. When an `mcpadapt` release for `mcp` 2 is locked, `load_tools` becomes
`MCPClient({...}).get_tools()` and this file goes away; the mapping and the handle stay as they are.

The tool is the chassis's: its name, description, and JSON Schema come from `tools/list`, and
nothing here defines one. Each MCP operation opens its own short session over streamable HTTP (the
chassis endpoint is stateless), with the run's headers (`traceparent`, the bearer) on every request.

Sync and async: smolagents calls a tool from the thread that runs the generated code, so `forward`
runs its own event loop (`asyncio.run`). Tool descriptions and results are data, never
instructions.
"""

from __future__ import annotations

import asyncio
import keyword
import logging
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from smolagents.tools import AUTHORIZED_TYPES, Tool  # type: ignore[import-untyped]

TOOL_URL_VAR = "CHASSIS_TOOL_URL"
DEFAULT_TOOL_URL = "http://127.0.0.1:8090/mcp"
log = logging.getLogger(__name__)

transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook. When set, MCP calls go through this transport (for example
`httpx2.AsyncHTTPTransport(uds=...)` to a FastMCP app served on a Unix socket) instead of TCP."""

list_failures = 0
"""How many runs found the tool endpoint unreachable and ran without tools (also logged)."""

OnCall = Callable[[str, dict[str, Any], dict[str, Any]], None]
"""Called after each MCP call: tool name, arguments, result (`{"error": ...}` on failure)."""
OnFailure = Callable[[BaseException], None]


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


def _inputs(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The tool's JSON Schema properties as smolagents `inputs` (`type` and `description`)."""
    required = set(schema.get("required") or [])
    out: dict[str, dict[str, Any]] = {}
    for name, prop in (schema.get("properties") or {}).items():
        kind = prop.get("type")
        entry: dict[str, Any] = {
            "type": kind if isinstance(kind, str) and kind in AUTHORIZED_TYPES else "any",
            "description": str(prop.get("description") or prop.get("title") or name),
        }
        if name not in required:
            entry["nullable"] = True
        out[name] = entry
    return out


def _python_name(name: str) -> str:
    safe = re.sub(r"\W", "_", name)
    return f"{safe}_" if keyword.iskeyword(safe) or safe[:1].isdigit() else safe


class McpTool(Tool):  # type: ignore[misc]
    """One chassis MCP tool. `forward` calls it and reports the call to `on_call`."""

    skip_forward_signature_validation = True
    output_type = "any"

    def __init__(
        self,
        *,
        mcp_name: str,
        description: str,
        schema: dict[str, Any],
        headers: dict[str, str],
        timeout_s: float,
        on_call: OnCall,
        on_failure: OnFailure,
    ) -> None:
        self.mcp_name = mcp_name
        self.name = _python_name(mcp_name)
        self.description = description or mcp_name
        self.inputs = _inputs(schema)
        self._headers = headers
        self._timeout_s = timeout_s
        self._on_call = on_call
        self._on_failure = on_failure
        super().__init__()

    def forward(self, *args: Any, **kwargs: Any) -> Any:
        names = list(self.inputs)
        arguments = {**dict(zip(names, args, strict=False)), **kwargs}
        arguments = {k: v for k, v in arguments.items() if v is not None}
        try:
            result = asyncio.run(self._call(arguments))
        except Exception as exc:  # the SDK wraps transport failures in an ExceptionGroup
            self._on_call(self.mcp_name, arguments, {"error": str(exc)[:200] or type(exc).__name__})
            self._on_failure(exc)
            raise
        self._on_call(self.mcp_name, arguments, result)
        return result

    async def _call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        async with _session(self._headers, self._timeout_s) as session:
            result = await session.call_tool(self.mcp_name, arguments)
        text = "".join(getattr(part, "text", "") for part in getattr(result, "content", []))
        if getattr(result, "is_error", False):
            raise RuntimeError(text[:200] or "tool error")
        structured = getattr(result, "structured_content", None)
        return structured if isinstance(structured, dict) else {"text": text}


async def load_tools(
    headers: dict[str, str],
    timeout_s: float,
    on_call: OnCall,
    on_failure: OnFailure,
) -> list[Tool]:
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
    return [
        McpTool(
            mcp_name=t.name,
            description=t.description or "",
            schema=t.input_schema,
            headers=headers,
            timeout_s=timeout_s,
            on_call=on_call,
            on_failure=on_failure,
        )
        for t in listed
    ]
