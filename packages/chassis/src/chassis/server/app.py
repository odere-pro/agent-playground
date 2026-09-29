"""The HTTP surface of the chassis: `/health`, `/ready`, `/v1/run` streaming and complete, and
the model pass-through `/v1/chat/completions` (`chassis.server.model_proxy`).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from chassis import CHASSIS_VERSION
from chassis.core.collector import collect
from chassis.core.envelope import Budget, Context, Request, Response, TaskInput, Versions
from chassis.core.events import Error, Event
from chassis.ports.bundle import PortBundle
from chassis.profiles import build_ports, check_lane
from chassis.server.config import ChassisConfig
from chassis.server.model_proxy import model_proxy_router


class RunRequest(BaseModel):
    """The `/v1/run` body: the native `Request` with the ids and the agent optional."""

    model_config = ConfigDict(extra="forbid")
    request_id: str | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None
    agent: str | None = None
    agent_version: str | None = None
    input: TaskInput
    context_ref: str | None = None
    stream: bool = False
    budget: Budget = Field(default_factory=Budget)

    def to_request(self, config: ChassisConfig) -> Request:
        return Request(
            request_id=self.request_id or uuid.uuid4().hex,
            trace_id=self.trace_id or uuid.uuid4().hex,
            idempotency_key=self.idempotency_key or uuid.uuid4().hex,
            agent=self.agent or config.agent.name,
            agent_version=self.agent_version or config.agent.version,
            input=self.input,
            context_ref=self.context_ref,
            stream=self.stream,
            budget=self.budget,
        )


def versions_for(config: ChassisConfig) -> Versions:
    return Versions(
        chassis=CHASSIS_VERSION,
        config=config.version,
        prompt=config.spec.prompt.version,
        model_route=config.spec.model.route,
    )


def context_for(request: Request, config: ChassisConfig) -> Context:
    return Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=versions_for(config),
        model_route=config.spec.model.route,
    )


async def _guarded(events: AsyncIterator[Event]) -> AsyncIterator[Event]:
    """Forward the engine's events; an exception becomes one `Error` event, not a 500."""
    try:
        async for event in events:
            yield event
    except Exception as exc:  # the pipeline owns errors; never a 500 on the stream
        yield Error(code="engine_error", message=f"{type(exc).__name__}: {exc}")


async def _replay(events: Sequence[Event]) -> AsyncIterator[Event]:
    for event in events:
        yield event


def _frame(name: str, payload: BaseModel) -> str:
    return f"event: {name}\ndata: {payload.model_dump_json()}\n\n"


def create_app(config: ChassisConfig, ports: PortBundle | None = None) -> FastAPI:
    """Build the app. Ports come from the profile unless a test injects them."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bundle = ports or build_ports(config.profile, config.spec.adapters, agent=config.agent.name)
        check_lane(config.spec.engine.connector, config.profile)
        await bundle.engine.setup(config.spec.engine.as_mapping(), bundle)
        app.state.ports = bundle
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            await bundle.engine.close()

    app = FastAPI(title="chassis", version=CHASSIS_VERSION, lifespan=lifespan)
    app.state.config = config
    app.state.ready = False

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        if app.state.ready:
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not ready"}, status_code=503)

    @app.post("/v1/run")
    async def run(body: RunRequest) -> Any:
        if not app.state.ready:
            return JSONResponse({"detail": "engine not ready"}, status_code=503)
        bundle: PortBundle = app.state.ports
        request = body.to_request(config)
        ctx = context_for(request, config)
        telemetry = bundle.telemetry
        versions = versions_for(config)

        if not request.stream:
            with telemetry.span("chassis.run", request_id=request.request_id, agent=request.agent):
                telemetry.counter("chassis.requests", agent=request.agent)
                response = await collect(
                    _guarded(bundle.engine.run(request, ctx)), request, versions
                )
            return JSONResponse(response.model_dump(mode="json"))

        async def sse() -> AsyncIterator[str]:
            seen: list[Event] = []
            with telemetry.span("chassis.run", request_id=request.request_id, agent=request.agent):
                telemetry.counter("chassis.requests", agent=request.agent)
                async for event in _guarded(bundle.engine.run(request, ctx)):
                    seen.append(event)
                    yield _frame(event.type, event)
                response: Response = await collect(_replay(seen), request, versions)
            yield _frame("response", response)

        return StreamingResponse(sse(), media_type="text/event-stream")

    app.include_router(model_proxy_router(app))
    return app
