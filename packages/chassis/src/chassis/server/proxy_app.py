"""The proxy app: what a workload calls, on a localhost-only listener next to the public port.

`create_proxy_app(public_app)` mounts the model pass-through (`POST /v1/chat/completions` and the
Anthropic proxy route `POST /v1/messages`, `chassis.server.model_proxy`) and the MCP tool
endpoint (`/mcp`, `chassis.server.tool_endpoint`) on its own FastAPI app. It shares
`public_app.state` (the ports, `ready`, `runs`, `config`) instead of copying it, and it has no
lifespan of its own: the public app's lifespan builds the ports and sets `ready`, and the proxy
answers 503 until then. The MCP server's lifespan is hooked into the public app's, so call this
before the public app starts.

`chassis serve` runs this app on `--proxy-host 127.0.0.1 --proxy-port 8090` (suggested: 8090),
so only a process in the chassis's network namespace (the sidecar workload) reaches it; the
public port never carries the proxies (`deploy/CLAUDE.md`, ADR-001 hard requirement 1).

When `state.ports.events` has `inbound_routes()` (the Dapr adapter), the proxy also serves them
under `/dapr/`: the callbacks daprd makes (`--app-port 8090`). The ports exist only once the
lifespan built them, so the routes are looked up per request; before that `/dapr/` answers 503,
and with an events port that has no inbound routes it answers 404. The public app never has them.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.responses import Response
from starlette.routing import Route, Router
from starlette.types import Receive, Scope, Send

from chassis import CHASSIS_VERSION
from chassis.server.model_proxy import model_proxy_router
from chassis.server.tool_endpoint import mount_tool_endpoint


def create_proxy_app(public_app: FastAPI) -> FastAPI:
    """The proxy app over the public app's state. Serve it with `lifespan="off"`."""
    proxy = FastAPI(title="chassis-proxy", version=CHASSIS_VERSION)
    proxy.state = public_app.state
    proxy.include_router(model_proxy_router(proxy))
    mount_tool_endpoint(proxy, lifespan_app=public_app)
    proxy.router.routes.append(Route("/dapr/{rest:path}", _EventsInbound(proxy)))
    return proxy


class _EventsInbound:
    """ASGI app: dispatch `/dapr/...` to the events port's `inbound_routes()`, when it has them."""

    def __init__(self, proxy: FastAPI) -> None:
        self._proxy = proxy
        self._for: Any = None
        self._router: Router | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        ports = getattr(self._proxy.state, "ports", None)
        # Not `ready`: daprd reads the subscription list once, and a failing workload probe at
        # that moment must not leave it empty. Only the ports must exist.
        if ports is None:
            await Response(status_code=503)(scope, receive, send)
            return
        routes = getattr(ports.events, "inbound_routes", None)
        if routes is None:
            await Response(status_code=404)(scope, receive, send)
            return
        if self._router is None or self._for is not ports.events:
            self._router = Router(routes=routes())
            self._for = ports.events
        await self._router(scope, receive, send)
