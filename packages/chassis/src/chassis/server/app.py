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

With `spec.events.result_events` the lifespan adds a `ResultPublisher` (`chassis.server.results`)
to `pipeline.on_finished`; on the way out it waits for pending publishes, bounded, before the
ports close.

`/health` is 200 while the process serves HTTP, draining included; it never looks at the
workload. `/ready` is 503 with a `reason` (`starting`, `draining`, `workload_unreachable`) until
the lifespan finished, from the moment `state.draining` is set (`chassis.server.lifecycle`), and
while the workload probe fails (`chassis.server.readiness`). New calls are still served while
draining: `state.ready` stays the pipeline's "engine set up" flag. While draining, every public
response carries `Connection: close` (`chassis.server.lifecycle.CloseWhenDraining`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
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
from chassis.server.interfaces import mount_interfaces
from chassis.server.lifecycle import CloseWhenDraining
from chassis.server.manifest import mount_manifest
from chassis.server.pipeline import RunPipeline
from chassis.server.readiness import ProbeSettings, ReadinessMonitor, Reason, not_ready_reason
from chassis.server.results import ResultPublisher


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
    """Close the engine, the model adapter when it has an `aclose()`, the state store, the
    event port, and the config port when it has an `aclose()` (S3Config holds a client), in
    that order. Each one closes even when an earlier one raises.
    """
    closers = [
        bundle.engine.close,
        getattr(bundle.model, "aclose", None),
        bundle.state.aclose,
        bundle.events.aclose,
        getattr(bundle.config, "aclose", None),
    ]
    async with AsyncExitStack() as stack:
        for closer in reversed(closers):  # the stack runs them last in, first out
            if closer is not None:
                stack.push_async_callback(closer)


def create_app(
    config: ChassisConfig,
    ports: PortBundle | None = None,
    *,
    probe: ProbeSettings | None = None,
) -> FastAPI:
    """Build the app. Ports come from the profile unless a test injects them. `probe` sets the
    workload probe's interval, timeout, and failure count (suggested defaults in `ProbeSettings`).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bundle = ports or build_ports(
            config.profile,
            config.spec.adapters,
            connector=config.spec.engine.connector,
            agent=config.agent.name,
        )
        try:
            check_engine(bundle, config, built=ports is None)
            await bundle.engine.setup(config.spec.engine.as_mapping(), bundle)
        except BaseException:
            await close_ports(bundle)
            raise
        app.state.ports = bundle
        app.state.draining = False  # a lifespan entered again (tests) is not draining
        monitor = ReadinessMonitor(bundle.engine, probe or ProbeSettings())
        app.state.readiness = monitor
        # Background tasks start here, after the engine is set up and before `ready`.
        reloader = ConfigReloader(app.state, bundle.config, config, telemetry=bundle.telemetry)
        try:
            await reloader.start()  # the first config; a bad store document fails startup
        except BaseException:
            await close_ports(bundle)
            raise
        app.state.config_reloader = reloader
        publisher = None  # result events: `spec.events` is restart-only, read once here
        if app.state.config.spec.events.result_events:
            publisher = ResultPublisher(app.state)
            app.state.pipeline.on_finished.append(publisher)
        app.state.result_publisher = publisher
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
            if publisher is not None:  # pending publishes go out, bounded, before the ports close
                app.state.pipeline.on_finished.remove(publisher)
                await publisher.aclose()
            await close_ports(bundle)

    app = FastAPI(title="chassis", version=CHASSIS_VERSION, lifespan=lifespan)
    app.state.config = config
    app.state.config_loaded = False  # set by the config loader in the lifespan
    app.state.config_reloader = None
    app.state.result_publisher = None  # set in the lifespan when `spec.events.result_events`
    app.state.ready = False
    app.state.draining = False
    app.state.readiness = None
    app.state.runs = RunRegistry()
    app.state.pipeline = RunPipeline(app.state)

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
    mount_interfaces(app, config)  # last
    app.add_middleware(CloseWhenDraining, state=app.state)  # public only, never the proxy app
    return app
