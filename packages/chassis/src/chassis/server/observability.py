"""`GET /metrics` on the public app, and the model usage it counts (PoC-8, 014 H-7).

**The route.** The Prometheus text format of the telemetry adapter's `MetricsRegistry`
(`chassis.metrics`), every sample labeled with this chassis's `agent`. An adapter with no
`metrics` serves the declared families with no samples. It is not in the OpenAPI document and
not an interface. It is on the public app only, never the proxy app or the remote listener.

**Who may read it.** suggested: `spec.observability.metrics_scope` unset (the default) means no
token, as for `/health` and `/ready`, so a Prometheus scraper needs no credential. `OpenMetrics`, an
ASGI middleware outside caller auth, answers it. With a scope set, the call goes through caller
auth like any other route (401 without a valid token), and the route answers 403 `forbidden` unless
the token has the scope. The PoC-7 backlog notes carry "/metrics scope" as open; this is the pick
until the user decides.

**Usage.** `count_usage` turns one model call's usage into `chassis.model_tokens{route, direction}`
and, when `spec.observability.prices` names the route, `chassis.model_cost_microusd{route}`. The
model proxy calls it once per call that reported usage, on both of its routes. The cost is an
integer count of micro-USD, so it fits `TelemetryPort.counter`. LiteLLM's response cost header is
not read: `ModelPort` does not carry it (a port change, not made here).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi import Request as HTTPRequest
from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from chassis.metrics import CONTENT_TYPE, MetricsRegistry
from chassis.ports.model import Usage
from chassis.ports.telemetry import TelemetryPort
from chassis.server.config import ChassisConfig, ModelPrice
from chassis.server.security import principal_of

__all__ = [
    "COST",
    "METRICS_PATH",
    "TOKENS",
    "OpenMetrics",
    "count_usage",
    "mount_metrics",
    "render_metrics",
]

METRICS_PATH = "/metrics"
TOKENS = "chassis.model_tokens"
COST = "chassis.model_cost_microusd"
FORBIDDEN = "the token does not grant the scope this route needs"


def render_metrics(state: Any) -> str:
    """The exposition for the app whose `state` this is."""
    config: ChassisConfig = state.config
    telemetry = getattr(getattr(state, "ports", None), "telemetry", None)
    registry = getattr(telemetry, "metrics", None)
    if not isinstance(registry, MetricsRegistry):
        registry = MetricsRegistry()
    return registry.render({"agent": config.agent.name})


def _metrics_scope(state: Any) -> str | None:
    config: ChassisConfig = state.config
    return config.spec.observability.metrics_scope


class OpenMetrics:
    """Pure ASGI: answers `GET /metrics` before caller auth when no scope is set."""

    def __init__(self, app: ASGIApp, *, state: Any) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in ("GET", "HEAD")
            and scope["path"] == METRICS_PATH
            and _metrics_scope(self.state) is None
        ):
            response = Response(render_metrics(self.state), media_type=CONTENT_TYPE)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def mount_metrics(app: FastAPI) -> None:
    """The route, and `OpenMetrics` as the outermost middleware. Call it after every other
    `add_middleware`, so the open path is outside caller auth."""

    @app.get(METRICS_PATH, include_in_schema=False)
    async def metrics(http: HTTPRequest) -> Response:
        needed = _metrics_scope(app.state)
        principal = principal_of(http.scope)
        if needed is not None and (principal is None or not principal.allows(needed)):
            headers = {"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{needed}"'}
            body = {"detail": {"code": "forbidden", "message": FORBIDDEN}}
            return JSONResponse(body, status_code=403, headers=headers)
        return Response(render_metrics(app.state), media_type=CONTENT_TYPE)

    app.add_middleware(OpenMetrics, state=app.state)


def count_usage(
    telemetry: TelemetryPort, route: str, usage: Usage, price: ModelPrice | None
) -> None:
    """Count one call's tokens, and its cost when the route has a price."""
    for direction, tokens in (("input", usage.input_tokens), ("output", usage.output_tokens)):
        if tokens > 0:
            telemetry.counter(TOKENS, tokens, route=route, direction=direction)
    if price is None:
        return
    cost = usage.input_tokens * price.input_per_mtok + usage.output_tokens * price.output_per_mtok
    microusd = round(cost)  # USD per million tokens times tokens is micro-USD
    if microusd > 0:
        telemetry.counter(COST, microusd, route=route)
