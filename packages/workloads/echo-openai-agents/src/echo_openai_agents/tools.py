"""The chassis's MCP tools for the Agents SDK, and the MCP test hook (PoC-6, 6a).

The tools are not defined here. The workload connects the SDK's own streamable-HTTP MCP server to
`CHASSIS_TOOL_URL`, so the model is offered whatever the chassis serves. Three deviations from the
SDK's defaults, each in `ChassisMcpServer`:

- A tool that fails ends the run. The SDK would turn the failure into text for the model and go on;
  here a failed `tools/call` (a transport error or an `isError` result) raises `ToolFailed`, which
  `mapping.error_event` maps to `tool_error`, as echo-python does.
- A tool list that cannot be fetched is an empty list, not an error: the run goes on with no tools,
  `list_failures` counts it, and a warning is logged.
- Results are the MCP `structuredContent` as one JSON object (`use_structured_content`).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx2
from agents.mcp import MCPServerStreamableHttp
from mcp.types import CallToolResult
from mcp.types import Tool as MCPTool

log = logging.getLogger(__name__)

transport: httpx2.AsyncBaseTransport | None = None
"""Test-only hook: when set, MCP requests go through this `httpx2` transport, not a socket."""

list_failures = 0
"""How many times the tool list could not be fetched, so a run went on with no tools."""


class ToolFailed(Exception):
    """A `tools/call` failed: the transport broke, or the tool returned `isError`."""


def count_list_failure(reason: object) -> None:
    global list_failures
    list_failures += 1
    log.warning("the chassis tool endpoint is unusable; the run goes on with no tools: %s", reason)


async def _drop_authorization(request: httpx2.Request) -> None:
    request.headers.pop("authorization", None)


class ChassisMcpServer(MCPServerStreamableHttp):
    """The SDK's streamable-HTTP MCP server with the deviations above."""

    async def list_tools(self, run_context: Any = None, agent: Any = None) -> list[MCPTool]:
        try:
            return await super().list_tools(run_context, agent)
        except Exception as exc:
            count_list_failure(exc)
            return []

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None, meta: dict[str, Any] | None = None
    ) -> CallToolResult:
        try:
            result = await super().call_tool(tool_name, arguments, meta)
        except Exception as exc:
            raise ToolFailed(f"tool {tool_name} failed: {exc}") from exc
        if result.is_error:
            detail = " ".join(str(getattr(c, "text", "")) for c in result.content).strip()
            raise ToolFailed(f"tool {tool_name} returned an error: {detail or 'no detail'}")
        return result


def build_server(
    url: str, headers: dict[str, str], timeout: float, *, token: str | None
) -> ChassisMcpServer:
    """The MCP server for one run. `headers` go on every request. With a remote `token` the
    `Authorization` header stays; without one any is dropped before the request leaves."""

    def client_factory(
        headers: dict[str, str] | None = None, timeout: Any = None, auth: Any = None
    ) -> httpx2.AsyncClient:
        # A fresh client each call: the SDK opens it once and closes it with the connection.
        return httpx2.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
            event_hooks={} if token else {"request": [_drop_authorization]},
        )

    return ChassisMcpServer(
        {
            "url": url,
            "headers": headers,
            "timeout": timeout,
            "sse_read_timeout": timeout,
            "httpx_client_factory": client_factory,
        },
        name="chassis-tools",
        client_session_timeout_seconds=timeout,
        use_structured_content=True,
        failure_error_function=None,
    )
