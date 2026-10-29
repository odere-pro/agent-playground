"""PoC-8 (014 H-7): the router span. Every model call through either proxy route opens
`chassis.model.route`: the route the workload asked for, the route the chassis calls, and whether
the run's fallback route forced it. It sits in the run's trace, next to `chassis.model.call`.

The other chassis spans already have tests: `chassis.run` (`test_otel_wiring.py`), the model proxy
`chassis.model.call` (`test_model_proxy.py::test_a_call_of_an_in_flight_run_gets_a_span_and_is_
charged`), and the tool proxy `chassis.tool.call` (`test_tool_endpoint.py`). Offline.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from chassis import CHASSIS_VERSION
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.model_proxy import ROUTE_SPAN
from chassis.server.proxy_app import create_proxy_app

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret
PARENT = "00f067aa0ba902b7"
TRACEPARENT = f"00-{TRACE}-{PARENT}-01"
CHAT = {"model": "big-default", "messages": [{"role": "user", "content": "hello"}]}
MESSAGES = {**CHAT, "max_tokens": 64}


def _ports() -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


def _run() -> tuple[Request, Context]:
    request = Request(
        request_id="req-1",
        trace_id=TRACE,
        idempotency_key="idem-1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="x"),
        budget=Budget(max_tokens=1000),
    )
    ctx = Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=Versions(chassis=CHASSIS_VERSION),
    )
    return request, ctx


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


@pytest.mark.parametrize(
    ("path", "body", "fmt"),
    [("/v1/chat/completions", CHAT, "openai"), ("/v1/messages", MESSAGES, "anthropic")],
)
@pytest.mark.parametrize("forced", [False, True], ids=["as-asked", "forced"])
async def test_every_proxied_model_call_has_a_route_span_in_the_run_trace(
    path: str, body: dict[str, Any], fmt: str, forced: bool
) -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = _run()
    async with (
        _client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        if forced:
            record.model_route = "fallback-route"
        res = await client.post(path, json=body, headers={"traceparent": TRACEPARENT})
    assert res.status_code == 200, res.text
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    (span,) = [s for s in telemetry.spans if s.name == ROUTE_SPAN]
    assert span.ended
    assert span.parent_id == PARENT, "a child of the workload's call, in the run's trace"
    attributes = span.attributes
    assert attributes["trace_id"] == TRACE and attributes["request_id"] == "req-1"
    assert attributes["route.requested"] == "big-default"
    assert attributes["route"] == ("fallback-route" if forced else "big-default")
    assert attributes["route.forced"] is forced
    assert attributes["format"] == fmt
    (call,) = [s for s in telemetry.spans if s.name == "chassis.model.call"]
    assert call.attributes["route"] == attributes["route"]


async def test_an_uncorrelated_call_has_a_route_span_too() -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        assert (await client.post("/v1/chat/completions", json=CHAT)).status_code == 200
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    (span,) = [s for s in telemetry.spans if s.name == ROUTE_SPAN]
    assert span.attributes["correlated"] is False
    assert span.attributes["route"] == "big-default"
