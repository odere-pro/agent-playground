"""PoC-7 chassis wiring of trace context (014 H-7), over `OtelTelemetry` and an in-memory exporter.

- `/v1/run` roots `chassis.run` in the request's trace, continues a caller's `traceparent`, and
  puts the session header on the span.
- The A2A connector sends the run's W3C `baggage` next to `traceparent`.
- The model proxy's `chassis.model.call` span continues the workload's `traceparent`.

No network: ASGI transports and a Unix socket.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import unquote_plus

import httpx
from a2a_uds import SIDECAR_URL, Recorder, serve_uds
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.adapters.otel import OtelTelemetry
from chassis.core.trace import trace_id_hex
from chassis.fakes import InMemoryConfig, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.helpers import make_context, make_request
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

CALLER_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
CALLER_SPAN = "00f067aa0ba902b7"
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


def _one(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == name]
    return span


def _ids(span: ReadableSpan) -> tuple[str, str]:
    assert span.context is not None
    return f"{span.context.trace_id:032x}", f"{span.context.span_id:016x}"


def _app(exporter: InMemorySpanExporter) -> Any:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=InProcessConnector(),
        config=InMemoryConfig(),
        telemetry=OtelTelemetry(span_exporters=[exporter]),
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


async def test_run_span_continues_the_callers_traceparent_and_names_the_session() -> None:
    exporter = InMemorySpanExporter()
    app = _app(exporter)
    headers = {
        "traceparent": f"00-{CALLER_TRACE}-{CALLER_SPAN}-01",
        "x-session-id": "sess-42",
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c") as client,
    ):
        r = await client.post("/v1/run", json={"input": {"text": "hi"}}, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    run = _one(exporter, "chassis.run")
    engine = _one(exporter, "chassis.engine.run")
    trace, run_span = _ids(run)
    assert trace == CALLER_TRACE
    assert run.parent is not None and f"{run.parent.span_id:016x}" == CALLER_SPAN
    assert run.attributes is not None and run.attributes["session.id"] == "sess-42"
    assert run.attributes["trace_id"] == CALLER_TRACE
    assert engine.parent is not None and f"{engine.parent.span_id:016x}" == run_span
    assert _ids(engine)[0] == CALLER_TRACE
    assert (engine.attributes or {}).get("session.id") == "sess-42"


async def test_a_run_with_no_caller_trace_is_a_root_in_its_own_trace_id() -> None:
    exporter = InMemorySpanExporter()
    app = _app(exporter)
    body = {"trace_id": "trace-from-body", "input": {"text": "hi"}}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c") as client,
    ):
        r = await client.post("/v1/run", json=body)
    assert r.status_code == 200
    run = _one(exporter, "chassis.run")
    assert run.parent is None
    assert _ids(run)[0] == trace_id_hex("trace-from-body")
    assert "session.id" not in (run.attributes or {})


async def test_the_connector_sends_baggage_next_to_the_traceparent() -> None:
    """The workload gets `user.id` and `session.id` as W3C baggage on the A2A request."""
    from chassis.core.handle import echo_wire

    exporter = InMemorySpanExporter()
    telemetry = OtelTelemetry(span_exporters=[exporter])
    recorder = Recorder(
        build_app(echo_wire, build_agent_card(name="t", version="1", url=SIDECAR_URL))
    )
    request = make_request(text="hi")
    async with serve_uds(recorder) as path:
        connector = SidecarConnector()
        bundle = PortBundle(
            model=ScriptedModel(), engine=connector, config=InMemoryConfig(), telemetry=telemetry
        )
        await connector.setup({"connector": "sidecar", "url": SIDECAR_URL, "uds": path}, bundle)
        try:
            with telemetry.span(
                "chassis.run",
                trace_id=trace_id_hex(request.trace_id),
                **{"user.id": "u-7", "session.id": "s-7"},
            ):
                async with asyncio.timeout(10):
                    events = [e async for e in connector.run(request, make_context(request))]
        finally:
            await connector.close()
    assert events[-1].type == "end"
    (seen,) = [s for s in recorder.rpc() if s.method == "SendStreamingMessage"]
    entries = dict(i.split("=", 1) for i in seen.headers["baggage"].split(","))
    assert {k: unquote_plus(v) for k, v in entries.items()} == {
        "user.id": "u-7",
        "session.id": "s-7",
    }
    engine = _one(exporter, "chassis.engine.run")
    trace, span_id = _ids(engine)
    assert seen.headers["traceparent"] == f"00-{trace}-{span_id}-01"


async def _chat(app: Any, headers: dict[str, str]) -> httpx.Response:
    proxy = create_proxy_app(app)
    body = {"model": "fake-route", "messages": [{"role": "user", "content": "x"}]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy), base_url="http://proxy"
    ) as client:
        return await client.post("/v1/chat/completions", json=body, headers=headers)


async def test_the_model_proxy_span_continues_the_workloads_traceparent() -> None:
    exporter = InMemorySpanExporter()
    app = _app(exporter)
    workload_span = "1111111111111111"
    async with app.router.lifespan_context(app):
        r = await _chat(app, {"traceparent": f"00-{CALLER_TRACE}-{workload_span}-01"})
    assert r.status_code == 200, r.text
    call = _one(exporter, "chassis.model.call")
    assert _ids(call)[0] == CALLER_TRACE
    assert call.parent is not None and f"{call.parent.span_id:016x}" == workload_span


async def test_a_bad_traceparent_on_the_proxy_records_no_parent() -> None:
    exporter = InMemorySpanExporter()
    app = _app(exporter)
    async with app.router.lifespan_context(app):
        r = await _chat(app, {"traceparent": "garbage"})
    assert r.status_code == 200
    call = _one(exporter, "chassis.model.call")
    assert call.parent is None
    assert "traceparent" not in (call.attributes or {})
