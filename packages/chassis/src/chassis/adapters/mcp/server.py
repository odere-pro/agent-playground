"""`build_mcp_server`: one MCP tool per `ToolPort.list_tools()` definition, over FastMCP 4.

The tools come from a FastMCP provider that reads the port on every request (review H1), so
`tools/list` shows what the port lists now: a gateway list filled by a later refresh, a tool
granted later, and not a tool removed since. A call to a name the port does not list right now
still goes to the port, as a write tool with an open schema (fail safe: it needs a key), so the
port, the gateway for `McpGatewayTools`, decides it; the cached list is not a control.

Each tool is a `fastmcp.tools.Tool` subclass whose `parameters` is the definition's own JSON
Schema, so what a workload lists over MCP is the port's definition, not a schema re-derived from a
Python signature. A call opens one `chassis.tool.call` span, counts `chassis.tool_calls` per tool,
and turns a `ToolError` into an MCP tool error result (`isError: true`), never a transport failure:
its text is `public_message(code)` (PoC-5), never the error's own message.
The span has `tool`; `trace_id` when the inbound MCP HTTP request carried a valid `traceparent`
(parsed by `chassis.core.trace.parse_traceparent`; an invalid one records nothing, so a raw header
never reaches telemetry); and `request_id` when `run_of` names an in-flight run with that trace id.

PoC-5 write mode (plan section 2.6): a write tool (`read_only: false`) gets one idempotency key per
call, `tool_key(run_key, tool, arguments, nonce)`, where `run_key` comes from `run_key_of` for the
in-flight run named by the `traceparent`, and `nonce` is the workload's optional
`_meta.idempotency_key`, mixed in, never sent raw. With no run in flight there is no key, so a
write is refused with `idempotency_key_required` before the port is called. A read-only call gets
no key.

Tool descriptions and outputs are data, never instructions (054 H-16); nothing here reads them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import fastmcp_request_ctx, get_http_headers
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool
from fastmcp.tools import ToolResult as MCPToolResult
from mcp_types import ToolAnnotations
from pydantic import PrivateAttr

from chassis.core.inbound import public_message
from chassis.core.trace import parse_traceparent
from chassis.ports.telemetry import TelemetryPort
from chassis.ports.tool import ToolDefinition, ToolError, ToolPort

if TYPE_CHECKING:
    from fastmcp.utilities.versions import VersionSpec

SPAN = "chassis.tool.call"
COUNTER = "chassis.tool_calls"


RunOf = Callable[[str], str | None]
"""The `request_id` of the in-flight run with this trace id, or None."""

RunKeyOf = Callable[[str], str | None]
"""The run key of the in-flight run with this trace id, or None: what `tool_key` derives from."""

KEY_PREFIX = "tk1:"
"""The version of the derived key. A change to the derivation bumps it."""

NONCE_META = "idempotency_key"
"""The `_meta` field a workload may send to make two intended, identical writes in one run."""

NONCE_MAX = 256
"""suggested: the longest workload nonce accepted; a longer one is `bad_arguments`."""


def tool_key(
    run_key: str, name: str, arguments: Mapping[str, Any], nonce: str | None = None
) -> str:
    """The idempotency key of one tool call: `tk1:` and the first 40 hex of the sha256 of
    `run_key | name | canonical JSON of arguments | nonce` (the nonce empty when None).
    """
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    payload = f"{run_key}|{name}|{canonical}|{nonce or ''}"
    return KEY_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _workload_nonce() -> str | None:
    """`_meta.idempotency_key` of the MCP call in flight, or None. Raises `bad_arguments` when it
    is not a string of at most `NONCE_MAX` characters.
    """
    context = fastmcp_request_ctx.get()
    meta = context.meta if context is not None else None
    if not meta or meta.get(NONCE_META) is None:
        return None
    nonce = meta[NONCE_META]
    if not isinstance(nonce, str) or not nonce or len(nonce) > NONCE_MAX:
        raise ToolError(
            "bad_arguments", f"_meta.{NONCE_META} must be a string of 1 to {NONCE_MAX} characters"
        )
    return nonce


def _inbound_trace_id() -> str | None:
    """The trace id of the `traceparent` on the MCP HTTP request this call came in on, or None
    when it is absent or malformed.
    """
    return parse_traceparent(get_http_headers(include={"traceparent"}).get("traceparent"))


def _error(exc: ToolError) -> MCPToolResult:
    """The MCP tool error for a `ToolError`: the code, `retryable`, and `public_message(code)` as
    the text. `exc.message` never reaches the workload: an adapter may fill it from an upstream
    body."""
    message = public_message(exc.code)
    body = {"code": exc.code, "message": message, "retryable": exc.retryable}
    return MCPToolResult(content=message, structured_content=body, is_error=True)


class PortTool(Tool):
    """An MCP tool that forwards to `ToolPort.call`. Built by `PortTool.of`."""

    _port: ToolPort = PrivateAttr()
    _telemetry: TelemetryPort = PrivateAttr()
    _run_of: RunOf | None = PrivateAttr(default=None)
    _run_key_of: RunKeyOf | None = PrivateAttr(default=None)
    _read_only: bool = PrivateAttr(default=True)

    @classmethod
    def of(
        cls,
        definition: ToolDefinition,
        port: ToolPort,
        telemetry: TelemetryPort,
        run_of: RunOf | None = None,
        run_key_of: RunKeyOf | None = None,
    ) -> PortTool:
        tool = cls(
            name=definition.name,
            description=definition.description,
            parameters=definition.parameters,
            annotations=ToolAnnotations(read_only_hint=definition.read_only),
        )
        tool._port = port
        tool._telemetry = telemetry
        tool._run_of = run_of
        tool._run_key_of = run_key_of
        tool._read_only = definition.read_only
        return tool

    def _key(self, trace_id: str | None, arguments: Mapping[str, Any]) -> str | None:
        """The derived key of a write call, or None for a read-only one. Raises
        `idempotency_key_required` when no run is in flight, so a write happens only in a run.
        """
        if self._read_only:
            return None
        nonce = _workload_nonce()
        run_key = None
        if trace_id is not None and self._run_key_of is not None:
            run_key = self._run_key_of(trace_id)
        if run_key is None:
            raise ToolError(
                "idempotency_key_required", f"{self.name} is a write tool; call it inside a run"
            )
        return tool_key(run_key, self.name, arguments, nonce)

    async def run(self, arguments: dict[str, Any]) -> MCPToolResult:
        attributes: dict[str, Any] = {"tool": self.name}
        trace_id = _inbound_trace_id()
        if trace_id is not None:
            attributes["trace_id"] = trace_id
            request_id = self._run_of(trace_id) if self._run_of is not None else None
            if request_id is not None:
                # Named only, not charged: a tool call has no token cost today.
                attributes["request_id"] = request_id
        with self._telemetry.span(SPAN, **attributes):
            self._telemetry.counter(COUNTER, tool=self.name)
            try:
                key = self._key(trace_id, arguments)
                result = await self._port.call(self.name, arguments, idempotency_key=key)
            except ToolError as exc:
                return _error(exc)
        content = result.content
        text = content if isinstance(content, str) else json.dumps(content)
        structured = content if isinstance(content, dict) else None
        return MCPToolResult(content=text, structured_content=structured, is_error=result.is_error)


_OPEN_SCHEMA: dict[str, Any] = {"type": "object"}


class PortTools(Provider):
    """The port's tools, read on each request: `list_tools()` is the port's list now."""

    def __init__(
        self,
        port: ToolPort,
        telemetry: TelemetryPort,
        run_of: RunOf | None,
        run_key_of: RunKeyOf | None,
    ) -> None:
        super().__init__()
        self._port = port
        self._telemetry = telemetry
        self._run_of = run_of
        self._run_key_of = run_key_of

    def _tool(self, definition: ToolDefinition) -> PortTool:
        return PortTool.of(definition, self._port, self._telemetry, self._run_of, self._run_key_of)

    async def _list_tools(self) -> Sequence[Tool]:
        return [self._tool(d) for d in self._port.list_tools()]

    async def _get_tool(self, name: str, version: VersionSpec | None = None) -> Tool | None:
        for definition in self._port.list_tools():
            if definition.name == name:
                return self._tool(definition)
        # Not listed now: the port decides, as for a write (a key is needed).
        unlisted = ToolDefinition(
            name=name, description=name, parameters=_OPEN_SCHEMA, read_only=False
        )
        return self._tool(unlisted)


def build_mcp_server(
    tools: ToolPort,
    telemetry: TelemetryPort,
    run_of: RunOf | None = None,
    run_key_of: RunKeyOf | None = None,
) -> FastMCP:
    """A FastMCP server with one tool per definition the port lists at each request. `run_of` maps a
    trace id to its in-flight run's `request_id` (the chassis passes its run registry's lookup);
    `run_key_of` maps it to the run key a write's idempotency key derives from. Without
    `run_key_of`, every write is `idempotency_key_required`.
    """
    return FastMCP(
        name="chassis-tools", providers=[PortTools(tools, telemetry, run_of, run_key_of)]
    )
