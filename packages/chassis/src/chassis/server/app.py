"""The public HTTP surface of the chassis: `/health`, `/ready`, and the interfaces.

The proxies a workload calls (the model proxy, `POST /v1/chat/completions` on the proxy port,
and the MCP tool endpoint, `/mcp` on the proxy port) are not here: they
live on the localhost-only proxy app (`chassis.server.proxy_app`), which shares this app's
`state`. Each `/v1/run` holds a `RunRecord` in `app.state.runs` for the life of the run, so the
proxies can charge a call to the run its `traceparent` names (`chassis.server.correlation`).
The run itself is `app.state.pipeline` (`chassis.server.pipeline`), shared by every inbound
adapter. The interfaces (native `/v1/run`, and the ones `spec.interfaces` turns on) are mounted
last by `chassis.server.interfaces.mount_interfaces`, just after `GET /manifest`
(`chassis.server.manifest`).

The lifespan checks the lane again once the ports exist (contract v1, decision 2), for a bundle
it built and for one a test injects: `check_lane(bundle.engine.kind, profile)`, and, when it
built the bundle, `bundle.engine.kind == spec.engine.connector`. A mismatch fails startup. On the
way out it closes the engine, the model adapter when it has an `aclose()`, then `ports.state` and
`ports.events`.

The config loader (`chassis.server.config_loader`) sets the first config in the lifespan, from the
store document or the bootstrap `config`, then swaps `app.state.config` on each accepted reload.
`/ready` is `starting` until the first config is set.

With `spec.harness.evaluator_gate` (PoC-7) the lifespan puts the engine behind the evaluator gate
(`chassis.server.evaluator_gate.with_gate`) once the first config is set; the gate refuses to start
with no evaluator.

With `spec.events.result_events` the lifespan adds a `ResultPublisher` (`chassis.server.results`)
to `pipeline.on_finished`; on the way out it waits for pending publishes, bounded, before the
ports close.

When `feedback_on(config)` (PoC-7, `chassis.server.feedback`), `create_app` mounts
`POST /v1/feedback` and the lifespan adds a `FeedbackLookup` to `pipeline.on_finished`, gives the
adapter `spec.feedback.score_name`, and, with `spec.feedback.events`, starts `FeedbackEvents`
(`app.state.feedback_events`), drained like the result publisher.

`/health` is 200 while the process serves HTTP, draining included; it never looks at the
workload. `/ready` is 503 with a `reason` (`starting`, `draining`, `workload_unreachable`) until
the lifespan finished, from the moment `state.draining` is set (`chassis.server.lifecycle`), and
while the workload probe fails (`chassis.server.readiness`). New calls are still served while
draining: `state.ready` stays the pipeline's "engine set up" flag. While draining, every public
response carries `Connection: close` (`chassis.server.lifecycle.CloseWhenDraining`).

With `spec.governance.marking` on, every response of a run route carries `X-AI-Generated` and
`X-AI-Disclosure-Required` (`chassis.server.marking.MarkOutputs`).

`GET /metrics` (PoC-8, `chassis.server.observability`) is the Prometheus text format of the
telemetry adapter's registry. Its `OpenMetrics` middleware is the outermost one, so with no
`spec.observability.metrics_scope` a scraper needs no token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from chassis import CHASSIS_VERSION
from chassis.ports.bundle import PortBundle
from chassis.profiles import LaneNotAllowed, build_ports, check_lane
from chassis.server.config import ChassisConfig
from chassis.server.config_loader import ConfigReloader
from chassis.server.correlation import RunRegistry
from chassis.server.evaluator_gate import with_gate
from chassis.server.feedback import (
    FeedbackEvents,
    FeedbackLookup,
    bind_score_name,
    feedback_on,
    mount_feedback,
)
from chassis.server.interfaces import mount_interfaces
from chassis.server.lifecycle import CloseWhenDraining
from chassis.server.manifest import mount_manifest
from chassis.server.marking import MarkOutputs
from chassis.server.observability import mount_metrics
from chassis.server.pipeline import RunPipeline
from chassis.server.rate_limit import install_rate_limit
from chassis.server.readiness import ProbeSettings, ReadinessMonitor, Reason, not_ready_reason
from chassis.server.results import ResultPublisher
from chassis.server.security import install_security


class Health(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ready", "not ready"]
    reason: Reason | None = None
    """Set on 503 only (contract v3, additive)."""


def check_engine(bundle: PortBundle, config: ChassisConfig, *, built: bool) -> None:
    """The lane the ports run is one the profile allows, and, when the chassis built the ports,
    the one `spec.engine.connector` names. Raises `LaneNotAllowed`.
    """
    kind = bundle.engine.kind
    check_lane(kind, config.profile)
    if built and kind != config.spec.engine.connector:
        raise LaneNotAllowed(
            f"the engine runs lane {kind!r}, but spec.engine.connector is "
            f"{config.spec.engine.connector!r}"
        )


async def close_ports(bundle: PortBundle) -> None:
    """Close the engine, the model adapter when it has an `aclose()`, the feedback adapter when it
    has one (PoC-7), the state store, the event port, the config port when it has an `aclose()`
    (S3Config holds a client), the evaluator when it has one (PoC-7), and last the telemetry
    adapter when it has one (`OtelTelemetry` flushes its spans), in that order. Each one closes
    even when an earlier one raises.
    """
    closers = [
        getattr(getattr(bundle, "telemetry", None), "aclose", None),
        bundle.engine.close,
        getattr(bundle.model, "aclose", None),
        getattr(getattr(bundle, "feedback", None), "aclose", None),
        bundle.state.aclose,
        bundle.events.aclose,
        getattr(bundle.config, "aclose", None),
        getattr(getattr(bundle, "evaluator", None), "aclose", None),
    ]
    async with AsyncExitStack() as stack:
        for closer in reversed(closers):  # the stack runs them last in, first out
            if closer is not None:
                stack.push_async_callback(closer)


def _build_ports(config: ChassisConfig) -> PortBundle:
    """The ports the profile and `spec.adapters` name, for the lane of `spec.engine.connector`."""
    return build_ports(
        config.profile,
        config.spec.adapters,
        connector=config.spec.engine.connector,
        agent=config.agent.name,
    )


def _init_state(app: FastAPI, config: ChassisConfig) -> None:
    """The state before the lifespan: not ready, not draining, no config loaded yet."""
    app.state.config = config
    app.state.config_loaded = False  # set by the config loader in the lifespan
    app.state.config_reloader = None
    app.state.result_publisher = None  # set in the lifespan when `spec.events.result_events`
    app.state.feedback_events = None  # set in the lifespan when `spec.feedback.events`
    app.state.ready = False
    app.state.draining = False
    app.state.readiness = None
    app.state.runs = RunRegistry()
    app.state.pipeline = RunPipeline(app.state)


def _start_result_events(app: FastAPI) -> ResultPublisher | None:
    """Add a `ResultPublisher` to `pipeline.on_finished` when `spec.events.result_events`.
    `spec.events` is restart-only, read once here."""
    publisher = None
    if app.state.config.spec.events.result_events:
        publisher = ResultPublisher(app.state)
        app.state.pipeline.on_finished.append(publisher)
    app.state.result_publisher = publisher
    return publisher


async def _stop_result_events(app: FastAPI, publisher: ResultPublisher | None) -> None:
    """Pending publishes go out, bounded, before the ports close."""
    if publisher is not None:
        app.state.pipeline.on_finished.remove(publisher)
        await publisher.aclose()


def _start_feedback(
    app: FastAPI, bundle: PortBundle, *, route: bool
) -> tuple[FeedbackLookup | None, FeedbackEvents | None]:
    """With the feedback route, name the score, add a `FeedbackLookup` to `pipeline.on_finished`,
    and, with `spec.feedback.events`, start `FeedbackEvents`. `spec.feedback` is restart-only,
    read once here."""
    lookup = None
    feedback_events = None
    if route:
        spec_feedback = app.state.config.spec.feedback
        bind_score_name(bundle.feedback, spec_feedback.score_name)
        lookup = FeedbackLookup(app.state)
        app.state.pipeline.on_finished.append(lookup)
        if spec_feedback.events:
            feedback_events = FeedbackEvents(app.state)
    app.state.feedback_events = feedback_events
    return lookup, feedback_events


async def _stop_feedback(
    app: FastAPI, lookup: FeedbackLookup | None, feedback_events: FeedbackEvents | None
) -> None:
    """Remove the lookup, then drain the feedback events, like the result publisher."""
    if lookup is not None:
        app.state.pipeline.on_finished.remove(lookup)
    if feedback_events is not None:
        app.state.feedback_events = None
        await feedback_events.aclose()


def _lifespan(
    config: ChassisConfig,
    ports: PortBundle | None,
    probe: ProbeSettings | None,
    *,
    feedback_route: bool,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The lifespan `create_app` gives FastAPI (module docstring)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bundle = ports or _build_ports(config)
        # A failed start unwinds what started, last in, first out: the reloader, then the ports.
        async with AsyncExitStack() as startup:
            startup.push_async_callback(close_ports, bundle)
            check_engine(bundle, config, built=ports is None)
            await bundle.engine.setup(config.spec.engine.as_mapping(), bundle)
            app.state.ports = bundle
            app.state.draining = False  # a lifespan entered again (tests) is not draining
            monitor = ReadinessMonitor(bundle.engine, probe or ProbeSettings())
            app.state.readiness = monitor
            # Background tasks start here, after the engine is set up and before `ready`.
            reloader = ConfigReloader(app.state, bundle.config, config, telemetry=bundle.telemetry)
            await reloader.start()  # the first config; a bad store document fails startup
            app.state.config_reloader = reloader
            startup.push_async_callback(reloader.stop)
            # PoC-7: the evaluator gate wraps the engine, by the first config
            bundle = app.state.ports = with_gate(bundle, app.state)
            startup.pop_all()  # started: the shutdown below owns them now
        publisher = _start_result_events(app)
        lookup, feedback_events = _start_feedback(app, bundle, route=feedback_route)
        monitor.start()
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            app.state.draining = True
            # Background tasks stop here, before the ports close.
            await reloader.stop()
            await monitor.stop()
            await _stop_result_events(app, publisher)
            await _stop_feedback(app, lookup, feedback_events)
            await close_ports(bundle)

    return lifespan


def create_app(
    config: ChassisConfig,
    ports: PortBundle | None = None,
    *,
    probe: ProbeSettings | None = None,
) -> FastAPI:
    """Build the app. Ports come from the profile unless a test injects them. `probe` sets the
    workload probe's interval, timeout, and failure count (suggested defaults in `ProbeSettings`).
    """
    feedback_route = feedback_on(config)
    lifespan = _lifespan(config, ports, probe, feedback_route=feedback_route)
    app = FastAPI(title="chassis", version=CHASSIS_VERSION, lifespan=lifespan)
    _init_state(app, config)

    @app.get("/health", operation_id="health", response_model=Health)
    async def health() -> Health:
        return Health(status="ok")

    @app.get(
        "/ready",
        operation_id="ready",
        response_model=Readiness,
        responses={
            503: {
                "model": Readiness,
                "description": "Starting, draining, or the workload does not answer (`reason`)",
            }
        },
    )
    async def ready() -> JSONResponse:
        reason = not_ready_reason(app.state)
        if reason is None:
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not ready", "reason": reason}, status_code=503)

    mount_manifest(app)
    if feedback_route:
        mount_feedback(app)  # not an interface: no `x-chassis-interface`, not in the manifest
    mount_interfaces(app, config)  # last
    install_rate_limit(app)  # PoC-8: per caller, inside auth (it reads the principal)
    install_security(app)  # PoC-7: caller auth and scopes, outside the body limit
    app.add_middleware(CloseWhenDraining, state=app.state)  # public only, never the proxy app
    app.add_middleware(MarkOutputs, state=app.state)  # a refusal is marked too
    mount_metrics(app)  # last: its open path is outside caller auth
    return app
