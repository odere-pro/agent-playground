"""ToolPort: the tools the chassis serves to a workload. Defined once here, served over MCP by
`chassis.adapters.mcp`. The real adapter (`McpGatewayTools`, an MCP gateway client, backlog
054 H-16) arrives in PoC-5.

PoC-5 (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 2.6), additive: `call` takes the
keyword `idempotency_key`. Write mode is `ToolDefinition.read_only = False` (MCP's
`readOnlyHint`). A write tool called without a key raises `ToolError("idempotency_key_required")`
before anything runs; with a key it has at most one effect per key, and a repeat returns the
first result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    """One tool. `parameters` is a JSON Schema object; it is the MCP input schema as is."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    read_only: bool = True


class ToolResult(BaseModel):
    """What a tool returned. `is_error` is a failure the tool reports, not a transport failure."""

    content: Any = None
    is_error: bool = False


class ToolError(Exception):
    """A call the port refused or could not make. `code`, and whether a retry may help:

    - `unknown_tool`: no such tool (not retryable).
    - `bad_arguments`: the arguments fail `parameters` (not retryable).
    - `idempotency_key_required` (PoC-5): a write tool (`read_only: false`) called without
      `idempotency_key` (not retryable).
    - `tool_denied` (PoC-5): the gateway refused the call for this credential: the tool is not
      on the key's allow-list, or the key is not valid (not retryable).
    - `tool_unavailable` (PoC-5): the gateway or the tool server did not answer, timed out, or
      failed with a 5xx (`retryable=True`).

    A failure the tool itself reports is `ToolResult(is_error=True)`, never a `ToolError`.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class ToolPort(Protocol):
    name: str

    def list_tools(self) -> Sequence[ToolDefinition]: ...

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> ToolResult:
        """Call one tool. `idempotency_key` is required for a write tool (`read_only: false`)."""
        ...


class NoTools:
    """The empty tool set: lists nothing, every call is `unknown_tool`. The `PortBundle` default,
    so a bundle built without tools serves an empty tool endpoint (`/mcp` on the proxy port).
    """

    name = "none"

    def list_tools(self) -> Sequence[ToolDefinition]:
        return ()

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
    ) -> ToolResult:
        raise ToolError("unknown_tool", f"no tool named {name!r}")
