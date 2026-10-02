"""`mount_tool_endpoint`: the MCP tool endpoint at `/mcp` on the proxy port, streamable HTTP, over
`ports.tools`. Not the agent's own MCP endpoint, `/v1/mcp` on the public port
(`chassis.server.interfaces.mcp`), which serves the agent as one tool to outside MCP clients.

The route goes on the localhost-only proxy app (`chassis.server.proxy_app`), never on the public
port, so a workload reaches tools the same way it reaches the model proxy. The proxy app has no
lifespan of its own, so the MCP server is built inside the lifespan of the app that has one (the
public app, `lifespan_app`), from the shared `state.ports.tools`: the tools are the ones the
profile or the test picked. FastMCP's own lifespan runs inside the chassis's. Before that
lifespan, and after it, `/mcp` answers 503.

PoC-5 write mode (plan section 2.6): a write call made inside a run gets one idempotency key,
derived by `chassis.adapters.mcp.server.tool_key` from `run_key_of(record, idempotency=...)`
(scoped to the agent and the request; the `request_id` when idempotency is off), the tool name, the
arguments, and the workload's optional nonce. A write with no run in flight (no `traceparent`, or
an unknown one) is refused with `idempotency_key_required`.

The MCP server reads `ports.tools.list_tools()` on every request, so `/mcp` lists what the port
lists now, not what it listed at startup (review H1). Every app that mounts `/mcp` over one
`lifespan_app` (the proxy, and the remote listener when it is on) shares one MCP server, and the
tools port is started and stopped once (review L1).

A tools port with a lifecycle (the gateway adapter, plan section 2.7: `refresh`, `start`,
`aclose`) is started here: the first `await refresh()` fills its cached list before `/mcp` opens,
`start()` begins the background refresh, and `aclose()` stops it on shutdown. A failed first
refresh does not stop the chassis; `/mcp` opens with the last list (empty at start),
`chassis.tools.refresh_failed` is counted, as it is for every later failed refresh, and the tools
appear on `/mcp` after the next refresh that succeeds.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from chassis.adapters.mcp import build_mcp_server
from chassis.ports.bundle import PortBundle
from chassis.ports.telemetry import TelemetryPort
from chassis.ports.tool import ToolPort
from chassis.server.correlation import RunRecord, RunRegistry

PATH = "/mcp"
REFRESH_FAILED = "chassis.tools.refresh_failed"

log = logging.getLogger(__name__)


def run_key_of(record: RunRecord, *, idempotency: bool) -> str:
    """The run key a write's idempotency key derives from (review M1).

    - Idempotency on: `record.keyed_run_key`, scoped to the agent and the request's fingerprint,
      so a replayed run (PoC-4 takeover, a new `request_id`) sends the same tool keys again and
      the tool server dedups the writes. A key the pipeline minted is a fresh uuid4, so that run
      key is never shared either.
    - Idempotency off (`spec.idempotency.enabled: false`): the `request_id`. Nothing replays, so a
      reused client key is a new run, and its writes are new effects.
    """
    if idempotency and record.keyed_run_key is not None:
        return record.keyed_run_key
    return record.request_id


async def _start_tools(tools: ToolPort, telemetry: TelemetryPort) -> None:
    """Refresh and start a tools port that has a lifecycle; leave any other port alone."""
    refresh = getattr(tools, "refresh", None)
    if refresh is None:
        return

    def count_failure() -> None:
        telemetry.counter(REFRESH_FAILED)

    hooked = hasattr(tools, "on_refresh_failed")
    if hooked:
        tools.on_refresh_failed = count_failure  # type: ignore[attr-defined]
    try:
        ok = bool(await refresh())
    except Exception as exc:  # a failed first refresh never stops startup
        log.warning("tool endpoint: first tools refresh raised %s", type(exc).__name__)
        count_failure()
    else:
        if not ok and not hooked:
            count_failure()
    start = getattr(tools, "start", None)
    if start is not None:
        start()


async def _stop_tools(tools: ToolPort) -> None:
    aclose = getattr(tools, "aclose", None)
    if aclose is not None:
        await aclose()


class _ToolEndpoint:
    """The `/mcp` route: 503 until the lifespan builds the MCP app, then that app."""

    def __init__(self) -> None:
        self.inner: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.inner is None:
            response = JSONResponse({"detail": "tool endpoint not ready"}, status_code=503)
            await response(scope, receive, send)
            return
        await self.inner(scope, receive, send)


_ENDPOINTS = "tool_endpoints"
"""The `state` attribute that holds the `/mcp` routes of one host app, so the tools port starts
once however many listeners mount `/mcp` (the proxy, and the remote listener when it is on)."""


def mount_tool_endpoint(app: FastAPI, lifespan_app: FastAPI | None = None) -> None:
    """Serve `state.ports.tools` over MCP at `/mcp` on `app`, once the lifespan of `lifespan_app`
    (default: `app`) has run. The two apps share `state` when they differ. Every app mounted over
    the same `lifespan_app` shares one MCP server, and the tools port starts and stops once.
    """
    host = lifespan_app or app
    endpoint = _ToolEndpoint()
    app.add_route(PATH, endpoint, include_in_schema=False)  # type: ignore[arg-type]
    endpoints: list[_ToolEndpoint] | None = getattr(host.state, _ENDPOINTS, None)
    if endpoints is not None:  # the host's lifespan is already wrapped: share it
        endpoints.append(endpoint)
        return
    endpoints = [endpoint]
    setattr(host.state, _ENDPOINTS, endpoints)
    outer = host.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        async with outer(a) as state:
            bundle: PortBundle = host.state.ports
            runs: RunRegistry = host.state.runs

            def run_of(trace_id: str) -> str | None:
                record = runs.lookup(trace_id)
                return record.request_id if record is not None else None

            def run_key_for(trace_id: str) -> str | None:
                record = runs.lookup(trace_id)
                if record is None:
                    return None
                # `spec.idempotency.enabled` is restart-only, so the live value is the run's.
                return run_key_of(record, idempotency=host.state.config.spec.idempotency.enabled)

            await _start_tools(bundle.tools, bundle.telemetry)
            try:
                mcp_app = build_mcp_server(
                    bundle.tools, bundle.telemetry, run_of, run_key_for
                ).http_app(path=PATH, stateless_http=True)
                async with mcp_app.router.lifespan_context(mcp_app):
                    for each in endpoints:
                        each.inner = mcp_app
                    try:
                        yield state
                    finally:
                        for each in endpoints:
                            each.inner = None
            finally:
                await _stop_tools(bundle.tools)

    host.router.lifespan_context = lifespan
