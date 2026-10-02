"""`build_agent_mcp`: the agent itself as one MCP tool, generated from the chassis's OpenAPI spec.

This is the agent's MCP interface, for a client that calls the agent (`/v1/mcp` on the public
port, mounted by `chassis.server.interfaces.mcp`). It is not `build_mcp_server` in `server.py`,
which serves the workload's tools (`ToolPort`) at `/mcp` on the localhost-only proxy port.

The tool comes from an OpenAPI spec, never from a hand-written definition. The caller passes the
app's own spec, restricted to `POST /v1/run` (`chassis.server.interfaces.mcp.run_operation_spec`):
the whole spec carries the OpenAI and Anthropic request unions and costs about 0.25 s to build,
which `FastMCP.from_fastapi` (it calls `app.openapi()`) would pay at every start-up. With no spec
given, `app.openapi()` is read, as `from_fastapi` does. The route maps keep only `POST /v1/run`
as a tool and exclude every other operation. The tool is named after the agent (`mcp_names`,
keyed by the `run` operation id) and described by the operation's `description`; its input schema
is the `RunRequest` body schema from the spec, untouched (no `mcp_component_fn`). FastMCP keeps
the body's `properties` and `required`, and drops the body's own `title`, `description`, and
`additionalProperties`.

Each call goes to `/v1/run` in process through `httpx2.ASGITransport(app)`, with the header
`x-chassis-interface: mcp` (the native route then reads `stream` as false, labels telemetry
`mcp`, and re-mints a body `trace_id` in use) and no client timeout: httpx2's 5 s default would cut
a longer run, and the run's own `budget.timeout_ms` bounds it. FastMCP merges the MCP request's
headers into the call (all but `authorization`, `cookie`, and a few hop headers), and in some paths
a caller's header wins over the client's own. So a request hook (`_run_headers`) runs last, after
every merge: it keeps only the transport headers the client sets and `FORWARDED_HEADERS`
(`traceparent`, `tracestate`, `Idempotency-Key`), drops everything else the caller sent, and sets
`x-chassis-interface: mcp` itself, so an MCP caller cannot claim another interface. A non-2xx answer
is an MCP tool error; a run that ends with `status: error` is a 200 envelope, so a tool result
that is not `isError` (a known gap, section 4).
"""

from __future__ import annotations

from typing import Any

import httpx2
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

__all__ = ["FORWARDED_HEADERS", "INTERFACE_HEADER", "RUN_OPERATION", "RUN_PATH", "build_agent_mcp"]

RUN_OPERATION = "run"
RUN_PATH = "/v1/run"
INTERFACE_HEADER = "x-chassis-interface"
"""The header the tool's calls carry; suggested (section 7). Same name as the OpenAPI extension."""

FORWARDED_HEADERS = frozenset({"traceparent", "tracestate", "idempotency-key"})
"""The MCP caller's headers the inner `/v1/run` call may carry: correlation only."""

_TRANSPORT_HEADERS = frozenset(
    {
        "host",
        "accept",
        "accept-encoding",
        "connection",
        "user-agent",
        "content-type",
        "content-length",
    }
)
"""The headers the tool's own client and request builder set."""


async def _run_headers(request: httpx2.Request) -> None:
    """The last word on the inner call's headers: transport and correlation headers only, and the
    interface marker set here, after every merge FastMCP did."""
    for name in {key.lower() for key in request.headers}:
        if name not in _TRANSPORT_HEADERS and name not in FORWARDED_HEADERS:
            del request.headers[name]
    request.headers[INTERFACE_HEADER] = "mcp"


def build_agent_mcp(
    app: Any, *, agent_name: str, openapi_spec: dict[str, Any] | None = None
) -> FastMCP:
    """A FastMCP server with one tool, `agent_name`, that calls `POST /v1/run` on `app` (an ASGI
    app; typed `Any` so this package needs no FastAPI import). The tool is generated from
    `openapi_spec`, which must hold the `run` operation; None reads `app.openapi()` now.
    """
    spec = app.openapi() if openapi_spec is None else openapi_spec
    # The client `FastMCP.from_fastapi` builds, with no 5 s timeout and the interface header;
    # `_run_headers` enforces both the header and the forwarding rule on every request.
    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://fastapi",
        headers={INTERFACE_HEADER: "mcp"},
        timeout=None,
        event_hooks={"request": [_run_headers]},
    )
    return FastMCP.from_openapi(
        spec,
        client=client,
        name=agent_name,
        route_maps=[
            RouteMap(methods=["POST"], pattern=rf"^{RUN_PATH}$", mcp_type=MCPType.TOOL),
            RouteMap(mcp_type=MCPType.EXCLUDE),
        ],
        mcp_names={RUN_OPERATION: agent_name},
    )
