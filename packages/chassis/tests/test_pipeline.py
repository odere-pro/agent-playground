"""The run pipeline every inbound adapter shares: open a run (409 refusal on a trace id in use),
the guarded engine events in one `chassis.run` span, the record closed when the run ends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from chassis.core.envelope import Context, Request, TaskInput
from chassis.core.events import Delta, End, Error, Event, Start
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.correlation import TraceIdInUse
from chassis.server.pipeline import RunPipeline, RunStream
from fastapi import FastAPI
from starlette.requests import ClientDisconnect

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}


def _app(engine: FakeEngine | None = None) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


def _request(pipeline: RunPipeline, request_id: str = "r1", **fields: Any) -> Request:
    return pipeline.to_request(
        input=TaskInput(text="hello world"), request_id=request_id, trace_id="f" * 32, **fields
    )


async def test_create_app_stores_one_pipeline_that_follows_readiness() -> None:
    app = _app()
    pipeline = app.state.pipeline
    assert isinstance(pipeline, RunPipeline) and pipeline.ready is False
    async with app.router.lifespan_context(app):
        assert pipeline.ready is True
        request = pipeline.to_request(input=TaskInput(text="x"))
        assert (request.agent, request.agent_version) == ("echo", "0.0.1")
        assert len(request.request_id) == len(request.trace_id) == 32
        ctx = pipeline.context_for(request)
        assert (ctx.trace_id, ctx.model_route) == (request.trace_id, "fake-route")
        assert ctx.versions == pipeline.versions_for()
        assert (ctx.versions.config, ctx.versions.prompt) == ("cfg-1", "p1")
    assert pipeline.ready is False


async def test_open_refuses_a_trace_id_in_use_before_the_engine_runs() -> None:
    started: list[str] = []

    async def note(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        started.append(ctx.request_id)
        yield End(status="ok")

    app = _app(FakeEngine(handle=note))
    async with app.router.lifespan_context(app):
        pipeline: RunPipeline = app.state.pipeline
        first = pipeline.open(_request(pipeline, "first"))
        with pytest.raises(TraceIdInUse) as refused:
            pipeline.open(_request(pipeline, "dup"))
        assert (refused.value.trace_id, refused.value.request_id) == ("f" * 32, "first")
        assert app.state.runs.lookup("f" * 32) is first.record
        first.close()
    assert started == []
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests_refused", reason="trace_id_in_use") == 1
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 0
    [log] = telemetry.logs
    assert log["level"] == "warning" and (log["request_id"], log["holder"]) == ("dup", "first")


async def test_events_hold_the_record_and_close_it_when_the_engine_ends() -> None:
    held: list[bool] = []
    holder: dict[str, Any] = {}

    async def look(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        held.append(holder["app"].state.runs.lookup(ctx.trace_id) is not None)
        yield Delta(text="hi")
        yield End(status="ok")

    app = _app(FakeEngine(handle=look))
    holder["app"] = app
    async with app.router.lifespan_context(app):
        pipeline: RunPipeline = app.state.pipeline
        run = pipeline.open(_request(pipeline))
        with pytest.raises(RuntimeError, match="not consumed"):
            await run.response()
        types = [event.type async for event in run.events()]
        assert len(app.state.runs) == 0
        response = await run.response()
        complete = await pipeline.open(_request(pipeline, "r2")).complete()
        assert len(app.state.runs) == 0
    assert types == ["start", "delta", "end"] and held == [True, True]
    assert response.status == complete.status == "ok"
    assert response.output == complete.output == {"text": "hi"}
    assert (response.request_id, complete.request_id) == ("r1", "r2")
    telemetry = _telemetry(app)
    runs = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert [s.attributes for s in runs] == [
        {"request_id": "r1", "agent": "echo", "interface": "native"},
        {"request_id": "r2", "agent": "echo", "interface": "native"},
    ]
    assert all(s.ended for s in runs)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 2


async def test_open_labels_the_run_with_its_interface() -> None:
    app = _app()
    async with app.router.lifespan_context(app):
        pipeline: RunPipeline = app.state.pipeline
        run = pipeline.open(_request(pipeline), interface="anthropic")
        assert run.interface == "anthropic"
        await run.complete()
    telemetry = _telemetry(app)
    [span] = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert span.attributes["interface"] == "anthropic"
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="anthropic") == 1
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="native") == 0


async def test_served_is_the_agent_and_versions_from_the_config() -> None:
    pipeline: RunPipeline = _app().state.pipeline
    served = pipeline.served
    assert (served.agent, served.agent_version) == ("echo", "0.0.1")
    assert served.versions == pipeline.versions_for()


async def test_close_is_idempotent_and_never_frees_another_run() -> None:
    app = _app()
    async with app.router.lifespan_context(app):
        pipeline: RunPipeline = app.state.pipeline
        first = pipeline.open(_request(pipeline, "first"))
        first.close()
        first.close()
        second = pipeline.open(_request(pipeline, "second"))
        first.close()
        assert app.state.runs.lookup("f" * 32) is second.record
        second.close()
        assert len(app.state.runs) == 0


async def test_a_stream_whose_body_is_never_iterated_frees_the_trace_id() -> None:
    iterated: list[str] = []

    async def body() -> AsyncIterator[str]:
        iterated.append("body")
        yield "never"

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        raise OSError("the client left")

    app = _app()
    async with app.router.lifespan_context(app):
        pipeline: RunPipeline = app.state.pipeline
        run = pipeline.open(_request(pipeline))
        stream = RunStream(run, body())
        assert stream.media_type == "text/event-stream"
        scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
        with pytest.raises(ClientDisconnect):
            await stream(scope, receive, send)
        assert iterated == [] and len(app.state.runs) == 0


async def test_a_stream_runs_its_close_hook_even_when_the_body_is_never_iterated() -> None:
    closed: list[str] = []

    async def body() -> AsyncIterator[str]:
        yield "never"

    async def on_close() -> None:
        closed.append("hook")

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        raise OSError("the client left")

    app = _app()
    async with app.router.lifespan_context(app):
        pipeline: RunPipeline = app.state.pipeline
        run = pipeline.open(_request(pipeline))
        stream = RunStream(run, body(), headers={"x-request-id": "r1"}, on_close=on_close)
        assert stream.headers["x-request-id"] == "r1"
        scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
        with pytest.raises(ClientDisconnect):
            await stream(scope, receive, send)
        assert closed == ["hook"] and len(app.state.runs) == 0


async def test_an_engine_exception_becomes_one_engine_error_event() -> None:
    async def boom(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        raise ValueError("kaput")

    app = _app(FakeEngine(handle=boom))
    async with app.router.lifespan_context(app):
        pipeline: RunPipeline = app.state.pipeline
        run = pipeline.open(_request(pipeline))
        events = [event async for event in run.events()]
        assert len(app.state.runs) == 0
        response = await run.response()
    assert [event.type for event in events] == ["start", "error"]
    error = events[-1]
    assert isinstance(error, Error) and error.code == "engine_error"
    assert error.message == "ValueError: kaput"
    assert response.status == "error"
    assert response.output["error"] == {"code": "engine_error", "message": "ValueError: kaput"}
