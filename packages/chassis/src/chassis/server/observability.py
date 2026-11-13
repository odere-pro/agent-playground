"""`GET /metrics` on the public app, and the model usage it counts (PoC-8, 014 H-7).

**The route.** The Prometheus text format of the telemetry adapter's `MetricsRegistry`
(`chassis.metrics`), every sample labeled with this chassis's `agent`. An adapter with no
`metrics` serves the declared families with no samples. It is not in the OpenAPI document and
not an interface. It is on the public app only, never the proxy app or the remote listener.

**Who may read it.** suggested: `spec.observability.metrics_scope` unset (the default) means no
token, as for `/health` and `/ready`, so a Prometheus scraper needs no credential. `OpenMetrics`, an
ASGI middleware outside caller auth, answers it. With a scope set, the call goes through caller
auth like any other route (401 without a valid token), and the route answers 403 `forbidden` unless
the token has the scope. The `cloud` profile sets the scope to `spec.security.read_scope`
(`agent:read`) when the document names none (CH-14, P-4 option 1), so there a scraper needs a
token with it; `fake` and `local` stay open.

**Usage.** `count_usage` turns one model call's usage into `chassis.model_tokens{route, direction}`
and, when `spec.observability.prices` names the route, `chassis.model_cost_microusd{route}`. The
model proxy calls it once per call that reported usage, on both of its routes. The cost is an
integer count of micro-USD, so it fits `TelemetryPort.counter`. LiteLLM's response cost header is
not read: `ModelPort` does not carry it (a port change, not made here).

**Zero series.** Every scrape adds 0 to `chassis_model_budget_exhausted_total{route}` for each
route `spec.model` declares (CH-16). The chassis otherwise creates a series on its first count,
so Prometheus would first see a route's budget refusal at 1, never 0, and the alert
`ChassisModelBudgetExhausted` (`increase(...[15m]) > 0`) would see no rise. Done at scrape time,
not once at startup, so a route added by a reload gets its 0 too.

The same holds for the evaluator gate (117 X-3): with `spec.harness.evaluator_gate` on, each
scrape adds 0 to `chassis_gate_runs_total{mode}` for both modes, to
`chassis_gate_first_attempts_total{mode, outcome}` (046 A-5) for both modes and both outcomes
(`pass`, `fail`; a streaming request counts `mode="async"` even when the configured mode is
sync), and to `chassis_gate_retries_total`,
`chassis_gate_fallbacks_total`, `chassis_gate_failed_open_total`, and
`chassis_gate_failed_closed_total`, so a future `increase()` or `rate()` rule on any of them has a
0 to rise from. With the gate off they stay absent: there is no gate to report on. Not seeded,
because the label set is not known up front:
`chassis_run_errors_total{interface, code}` (the error codes are open: a workload's own codes pass
through) and `chassis_tool_calls_total{tool}` (the tools are the tool server's, not in the
config). An alert on either must not need the first sample at 0 (for example a ratio over
`chassis_requests_total`, or `chassis_run_duration_seconds_count{status="error"}`).

**Build info.** `chassis_build_info{version, agent_version}` is a gauge at 1: `version` is the
chassis release (`chassis.CHASSIS_VERSION`), `agent_version` is `agent.version` (restart-only, so
one series per process). An alert rule joins it to carry the chassis version (117 X-3).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi import Request as HTTPRequest
from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from chassis import CHASSIS_VERSION
from chassis.metrics import CONTENT_TYPE, MetricsRegistry
from chassis.ports.model import Usage
from chassis.ports.telemetry import TelemetryPort
from chassis.server.config import ChassisConfig, HarnessSpec, ModelPrice
from chassis.server.evaluator_gate import (
    FIRST_ATTEMPT_OUTCOMES,
    GATE_FAILED_CLOSED,
    GATE_FAILED_OPEN,
    GATE_FALLBACKS,
    GATE_FIRST_ATTEMPTS,
    GATE_RETRIES,
    GATE_RUNS,
)
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


IN_FLIGHT = "chassis_runs_in_flight"
"""CH-15: the runs in flight on this replica, a gauge read from the run registry at scrape time.
The chart's HPA can scale on it (`hpa.runsInFlight`)."""


BUDGET_SERIES = "chassis_model_budget_exhausted_total"
"""The family `model_admission.note_service_budget` counts (`chassis.model_budget_exhausted`)."""

BUILD_INFO = "chassis_build_info"
"""A gauge at 1 whose labels name the chassis release and the agent version (117 X-3)."""

GATE_SERIES: tuple[str, ...] = (GATE_RETRIES, GATE_FALLBACKS, GATE_FAILED_OPEN, GATE_FAILED_CLOSED)
"""The gate counters with no label of their own, created at 0 when the gate is on."""

GATE_MODES: tuple[str, ...] = ("sync", "async")
"""Every `mode` the gate counts runs under, whatever `evaluator_mode` is: streaming is async."""


def render_metrics(state: Any) -> str:
    """The exposition for the app whose `state` this is. The live gauges are set first, and the
    counters whose label set the config declares are created at 0 (a no-op once they exist)."""
    config: ChassisConfig = state.config
    telemetry = getattr(getattr(state, "ports", None), "telemetry", None)
    registry = getattr(telemetry, "metrics", None)
    if not isinstance(registry, MetricsRegistry):
        registry = MetricsRegistry()
    runs = getattr(state, "runs", None)
    if runs is not None:
        registry.gauge(IN_FLIGHT, len(runs))
    for route in config.spec.model.declared_routes():
        registry.count(BUDGET_SERIES, 0, {"route": route})
    _seed_gate(registry, config.spec.harness)
    registry.gauge(
        BUILD_INFO, 1, {"version": CHASSIS_VERSION, "agent_version": config.agent.version}
    )
    return registry.render({"agent": config.agent.name})


def _seed_gate(registry: MetricsRegistry, harness: HarnessSpec) -> None:
    """The gate counters at 0, so `increase()` sees the first run, first-attempt pass, retry,
    fallback, or fail."""
    if not harness.evaluator_gate:
        return
    for mode in GATE_MODES:
        registry.count(GATE_RUNS, 0, {"mode": mode})
        for outcome in FIRST_ATTEMPT_OUTCOMES:
            registry.count(GATE_FIRST_ATTEMPTS, 0, {"mode": mode, "outcome": outcome})
    for name in GATE_SERIES:
        registry.count(name, 0)


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
