"""Exit criterion 9, the offline half: a hung workload makes `/ready` false, `/health` stays 200,
and `/ready` comes back when the workload answers again (plan section 7).

A real chassis app with a real `SidecarConnector` probes a real workload template server
(`workload_a2a`, serving the chassis echo) on a Unix socket. The hang is played by a gate in front
of the workload app: while it is shut, every request waits and never answers, as with
`docker pause`. No hang switch goes into the workload. The probe settings are small so the test
runs in well under a second of probing.

The restart itself (the workload's own exec liveness probe in kind) is `test_kind.py`, `network`.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from chassis.adapters.a2a import SidecarConnector, build_agent_card
from chassis.adapters.a2a.inprocess import load_handle
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.readiness import ProbeSettings
from poc02_harness import SIDECAR_URL, on_unix_socket

HANDLE = "chassis.core.handle:echo_wire"
FAST = ProbeSettings(interval_s=0.02, timeout_s=0.1, failures=3)


class Gate:
    """ASGI middleware: while shut, every request waits without an answer. A thread event, since
    the workload runs on its own loop in its own thread.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.open = threading.Event()
        self.open.set()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and not self.open.is_set():
            await asyncio.to_thread(self.open.wait)
        await self.app(scope, receive, send)


@contextmanager
def gated_workload() -> Iterator[tuple[Gate, str]]:
    from workload_a2a.server import build_app

    card = build_agent_card(name=HANDLE, version="0.0.1", url=SIDECAR_URL)
    gate = Gate(build_app(load_handle(HANDLE), card))
    try:
        with on_unix_socket(gate, "a2a.sock") as uds:
            yield gate, uds
    finally:
        gate.open.set()


def _app(uds: str) -> Any:
    config = ChassisConfig.model_validate(
        {
            "version": "cfg-poc4",
            "profile": "fake",
            "agent": {"name": "echo", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds},
                "model": {"route": "fake-route"},
                "prompt": {"version": "p1"},
            },
        }
    )
    ports = PortBundle(
        model=ScriptedModel(),
        engine=SidecarConnector(),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(config, ports, probe=FAST)


async def _wait_for_ready(client: httpx.AsyncClient, status: int) -> httpx.Response:
    for _ in range(300):
        response = await client.get("/ready")
        if response.status_code == status:
            return response
        await asyncio.sleep(0.01)
    raise AssertionError(f"/ready never answered {status}: {response.json()}")


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


async def test_ready_goes_false_when_the_workload_hangs_and_health_stays_ok() -> None:
    with gated_workload() as (gate, uds):
        app = _app(uds)
        async with _client(app) as client, app.router.lifespan_context(app):
            assert (await client.get("/ready")).status_code == 200
            gate.open.clear()
            down = await _wait_for_ready(client, 503)
            assert down.json() == {"status": "not ready", "reason": "workload_unreachable"}
            health = await client.get("/health")
            assert (health.status_code, health.json()) == (200, {"status": "ok"})
            gate.open.set()


async def test_ready_comes_back_when_the_workload_answers() -> None:
    with gated_workload() as (gate, uds):
        app = _app(uds)
        async with _client(app) as client, app.router.lifespan_context(app):
            gate.open.clear()
            await _wait_for_ready(client, 503)
            gate.open.set()
            back = await _wait_for_ready(client, 200)
            assert back.json() == {"status": "ready"}
            run = await client.post(
                "/v1/run",
                json={"agent": "echo", "input": {"text": "hi"}},
            )
            assert run.status_code == 200, run.text
