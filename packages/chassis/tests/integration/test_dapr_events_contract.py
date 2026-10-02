"""`DaprEvents` passes the same `EventPort` contract suite as `InMemoryBus` and `KafkaEvents`,
against a real daprd next to a real Kafka in testcontainers. A `network` test: run it with
`make test-integration`.

The test process serves the routes daprd calls back (`inbound_routes()`) with uvicorn on
`127.0.0.1:<free port>`; daprd reaches it at `host.docker.internal`. daprd reads the subscription
list only when it starts, so the binding restarts daprd before the first publish after a
`subscribe` (`_RestartOnSubscribe`). That is the Dapr model, not a test trick: a chassis must
subscribe before daprd starts reading, and a new topic needs a daprd restart.

A case that cannot pass on Dapr is marked `xfail(strict=True)` with the reason: a finding for the
Dapr decision (ADR-004), never a weaker suite.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import uvicorn
from chassis.adapters.dapr import DaprEvents
from chassis.ports.events import CloudEvent, EventHandler, EventPort, Subscription
from chassis_contracts.containers.dapr import DaprSidecar, dapr_with_kafka
from chassis_contracts.events import EventPortContract
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Router
from starlette.types import Receive, Scope, Send

COMPONENTS = Path(__file__).resolve().parents[4] / "deploy" / "compose" / "dapr"
API_TOKEN = secrets.token_hex(16)
APP_TOKEN = secrets.token_hex(16)


class _Current:
    """The app daprd calls: the routes of whichever `DaprEvents` the running test built."""

    def __init__(self) -> None:
        self.port: DaprEvents | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.port is None:
            await JSONResponse([])(scope, receive, send)
            return
        await Router(routes=self.port.inbound_routes())(scope, receive, send)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def current() -> _Current:
    return _Current()


@pytest.fixture(scope="module")
def app_port(current: _Current) -> Iterator[int]:
    port = _free_port()
    app = Starlette(routes=[Route("/dapr/{rest:path}", current)])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield port
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def sidecar(app_port: int) -> Iterator[DaprSidecar]:
    with dapr_with_kafka(COMPONENTS, app_port, api_token=API_TOKEN, app_token=APP_TOKEN) as running:
        yield running


class _RestartOnSubscribe:
    """`DaprEvents`, plus a daprd restart before the first publish after a subscribe, then a
    wait until daprd's metadata lists every subscribed topic."""

    def __init__(self, port: DaprEvents, sidecar: DaprSidecar) -> None:
        self.port = port
        self.sidecar = sidecar
        self.topics: set[str] = set()
        self.dirty = False

    async def subscribe(
        self, topic: str, handler: EventHandler, *, group: str, max_attempts: int = 3
    ) -> Subscription:
        sub = await self.port.subscribe(topic, handler, group=group, max_attempts=max_attempts)
        self.topics.add(topic)
        self.dirty = True
        return sub

    async def publish(self, topic: str, event: CloudEvent) -> None:
        if self.dirty:
            await asyncio.to_thread(self.sidecar.restart)
            self.port.endpoint = self.sidecar.endpoint
            deadline = time.monotonic() + 30
            while not self.topics <= await asyncio.to_thread(self.sidecar.subscribed_topics):
                if time.monotonic() > deadline:
                    raise TimeoutError(f"daprd did not subscribe to {sorted(self.topics)}")
                await asyncio.sleep(0.25)
            self.dirty = False
        await self.port.publish(topic, event)

    async def aclose(self) -> None:
        await self.port.aclose()


class TestDaprEvents(EventPortContract):
    @pytest.fixture
    async def event_port(self, sidecar: DaprSidecar, current: _Current) -> AsyncIterator[EventPort]:
        port = DaprEvents(sidecar.endpoint, api_token=API_TOKEN, app_token=APP_TOKEN)
        current.port = port
        yield _RestartOnSubscribe(port, sidecar)
        await port.aclose()

    @pytest.fixture
    async def broken_event_port(self) -> AsyncIterator[EventPort]:
        port = DaprEvents(
            "http://127.0.0.1:1",
            api_token=API_TOKEN,
            app_token=APP_TOKEN,
            publish_backoff_s=(0.1, 0.1),
            timeout_s=2.0,
        )
        yield port
        await port.aclose()

    @pytest.fixture
    def deliver_timeout_s(self) -> float:
        return 30.0

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Dapr finding: daprd dead-letters the event as it was published, with no "
            "deadletterreason or deadletterattempts, and after maxRetries + 1 deliveries from "
            "resiliency.yaml (3), not the max_attempts given to subscribe (2)"
        ),
    )
    async def test_after_max_attempts_the_event_goes_to_the_dead_letter_topic(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float, quiet_s: float
    ) -> None:
        await super().test_after_max_attempts_the_event_goes_to_the_dead_letter_topic(
            event_port, topic, deliver_timeout_s, quiet_s
        )

    @pytest.mark.xfail(
        strict=True,
        raises=ValueError,
        reason=(
            "Dapr finding: the consumer group is daprd's app id, so one app holds one group per "
            "topic; a second group needs a second daprd (another app id)"
        ),
    )
    async def test_two_groups_each_get_every_event(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float
    ) -> None:
        await super().test_two_groups_each_get_every_event(event_port, topic, deliver_timeout_s)

    @pytest.fixture
    def quiet_s(self) -> float:
        return 2.0
