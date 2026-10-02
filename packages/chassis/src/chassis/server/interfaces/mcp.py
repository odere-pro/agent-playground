"""The MCP interface: the agent as one MCP tool at `/v1/mcp` on the public port (PoC-3 open note,
sections 5 and 7). suggested: the path.

This is for a client that calls the agent. It is not the tool endpoint (`/mcp` on the
localhost-only proxy port, `chassis.server.tool_endpoint`), which serves the workload's tools; the
two paths differ on purpose.

`mount_agent_mcp(app, config)` builds the tool from the app's own OpenAPI spec
(`chassis.adapters.mcp.build_agent_mcp`), restricted to `POST /v1/run` (`run_operation_spec`):
FastAPI's own generator, the one `app.openapi()` runs, over that one route. The whole spec carries
the OpenAI and Anthropic request unions and costs about 0.25 s, so start-up never builds it; it is
built when `/openapi.json` or `/manifest` asks. `/v1/run` must be mounted first; it runs last in
`mount_interfaces`. The server is kept at `app.state.agent_mcp` (the manifest lists its tools).
`/v1/mcp` is streamable HTTP, stateless (replicas share nothing), and not in
the OpenAPI spec. Its streamable HTTP app is built inside the public app's lifespan, chained into
it, because a session manager runs once; before that lifespan, and after it, `/v1/mcp` answers
503.

The call path: MCP client -> `/v1/mcp` -> the generated tool -> `httpx2.ASGITransport(app)` ->
`POST /v1/run` with `x-chassis-interface: mcp` -> `RunPipeline`. One in-process hop, no socket,
the same in every lane.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from chassis.adapters.mcp import build_agent_mcp
from chassis.adapters.mcp.agent import RUN_PATH
from chassis.server.config import ChassisConfig

__all__ = ["MCP_PATH", "MCP_TRANSPORT", "STATE_KEY", "mount_agent_mcp", "run_operation_spec"]

MCP_PATH = "/v1/mcp"
MCP_TRANSPORT = "streamable-http"
STATE_KEY = "agent_mcp"
"""`app.state.agent_mcp`: the agent's FastMCP server, or absent when MCP is switched off."""


class _AgentEndpoint:
    """The `/v1/mcp` route: 503 until the lifespan builds the streamable HTTP app, then that app."""

    def __init__(self) -> None:
        self.inner: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.inner is None:
            response = JSONResponse({"detail": "agent MCP endpoint not ready"}, status_code=503)
            await response(scope, receive, send)
            return
        await self.inner(scope, receive, send)


def run_operation_spec(app: FastAPI) -> dict[str, Any]:
    """`app`'s OpenAPI spec with `POST /v1/run` alone: its path item and the components it
    references, each as `app.openapi()` gives it (a test holds them equal). Raises `LookupError`
    when `/v1/run` is not mounted.
    """
    routes = [
        context
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute) and context.path == RUN_PATH
    ]
    if not routes:
        raise LookupError(f"{RUN_PATH} is not mounted; mount it before the agent MCP")
    return get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        routes=routes,
        separate_input_output_schemas=app.separate_input_output_schemas,
    )


def mount_agent_mcp(app: FastAPI, config: ChassisConfig) -> None:
    """Serve the agent as one MCP tool at `/v1/mcp` on `app`. Call it after `/v1/run` is mounted."""
    server = build_agent_mcp(
        app, agent_name=config.agent.name, openapi_spec=run_operation_spec(app)
    )
    setattr(app.state, STATE_KEY, server)
    endpoint = _AgentEndpoint()
    app.add_route(MCP_PATH, endpoint, include_in_schema=False)  # type: ignore[arg-type]
    outer = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        async with outer(a) as state:
            http_app = server.http_app(path=MCP_PATH, stateless_http=True)
            async with http_app.router.lifespan_context(http_app):
                endpoint.inner = http_app
                try:
                    yield state
                finally:
                    endpoint.inner = None

    app.router.lifespan_context = lifespan
