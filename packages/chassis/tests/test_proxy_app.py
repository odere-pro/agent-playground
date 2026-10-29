"""The proxy app's `/dapr/` dispatch (`chassis.server.proxy_app._EventsInbound`, review R2-32): 503
before the public app's lifespan built the ports, and the router rebuilt when `ports.events`
changes. The 404 branches are in `test_dapr_inbound.py`. Over `httpx.ASGITransport`, no socket.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}


class _RoutedBus(InMemoryBus):
    """An events port with inbound routes: `GET /dapr/subscribe` answers the bus's own name."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def inbound_routes(self) -> list[Route]:
        async def subscribe(_: Request) -> PlainTextResponse:
            return PlainTextResponse(self.label)

        return [Route("/dapr/subscribe", subscribe, methods=["GET"])]


def _public(events: InMemoryBus) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        events=events,
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy")


async def test_proxy_app_answers_503_on_dapr_when_the_public_app_has_not_started() -> None:
    """Before the lifespan builds the ports, `/dapr/subscribe` is 503."""
    proxy = create_proxy_app(_public(_RoutedBus("first")))

    async with _client(proxy) as client:
        answer = await client.get("/dapr/subscribe")

    assert answer.status_code == 503


async def test_proxy_app_serves_the_new_ports_routes_when_ports_events_changes() -> None:
    """A new `ports.events` gets its own router: the next call reaches the new port's routes."""
    public = _public(_RoutedBus("first"))
    proxy = create_proxy_app(public)

    async with public.router.lifespan_context(public), _client(proxy) as client:
        before = await client.get("/dapr/subscribe")
        public.state.ports = dataclasses.replace(public.state.ports, events=_RoutedBus("second"))
        after = await client.get("/dapr/subscribe")

    assert (before.text, after.text) == ("first", "second")
