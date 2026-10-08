"""Exit criterion 6, the chassis half: stopping a pair fails nothing in flight (plan section 6).

A real chassis pair (the public server and the proxy server, both `QuietServer`s on Unix sockets,
in this test's loop so it can take a real SIGTERM) runs a real workload template server
(`workload_a2a`) in the `sidecar` lane. The workload's `handle` streams one delta, waits until the
test lets it go, then calls the chassis's model proxy with the run's `traceparent`, and ends.

SIGTERM arrives mid-run. `/ready` answers 503 `draining` first; the public listener closes after
the drain delay (every public response in between carries `Connection: close`, the proxy's do
not); the run still finishes, including its model call, because the proxy listener
stays up until the public server has stopped. The workload side of the drain is
`test_workload_drain.py`; the Compose drill is `test_compose_scale.py` (`network`).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import tempfile
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from chassis.adapters.a2a import SidecarConnector, build_agent_card
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.core.handle import wire
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.lifecycle import Drain, DrainSettings, QuietServer
from chassis.server.proxy_app import create_proxy_app
from poc02_harness import SIDECAR_URL, on_unix_socket

ROUTE = "fake-route"


class SlowWorkload:
    """A typed `handle`: `start`, one delta, wait for `release`, one model call through the
    chassis proxy, the reply as a delta, `end`. Runs on the workload's own loop and thread.
    """

    def __init__(self, proxy_uds: str) -> None:
        self.proxy_uds = proxy_uds
        self.release = threading.Event()
        self.waiting = threading.Event()
        self.model_status: int | None = None
        self.model_connection: str | None = None

    async def __call__(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        yield Delta(text="half ")
        self.waiting.set()
        await asyncio.to_thread(self.release.wait)
        transport = httpx.AsyncHTTPTransport(uds=self.proxy_uds)
        async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": ROUTE, "messages": [{"role": "user", "content": "x"}]},
                headers={"traceparent": ctx.traceparent or ""},
            )
        self.model_status = response.status_code
        self.model_connection = response.headers.get("connection")
        reply = response.json()["choices"][0]["message"]["content"]
        yield Delta(text=reply)
        yield End(status="ok")


@contextmanager
def pair_and_workload(
    settings: DrainSettings,
) -> Iterator[tuple[Drain, SlowWorkload, str, str]]:
    from workload_a2a.server import build_app

    with tempfile.TemporaryDirectory(prefix="p4g-") as folder:  # short: macOS caps the path
        public_uds = str(Path(folder, "public.sock"))
        proxy_uds = str(Path(folder, "proxy.sock"))
        workload = SlowWorkload(proxy_uds)
        card = build_agent_card(name="slow", version="0.0.1", url=SIDECAR_URL)
        try:
            with on_unix_socket(build_app(wire(workload), card), "a2a.sock") as uds:
                config = ChassisConfig.model_validate(
                    {
                        "version": "cfg-poc4",
                        "profile": "fake",
                        "agent": {"name": "echo", "version": "0.0.1"},
                        "spec": {
                            "engine": {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds},
                            "model": {"route": ROUTE},
                            "prompt": {"version": "p1"},
                        },
                    }
                )
                ports = PortBundle(
                    model=ScriptedModel(default_reply="done"),
                    engine=SidecarConnector(),
                    config=InMemoryConfig(),
                    telemetry=InMemoryTelemetry(),
                )
                app = create_app(config, ports)
                proxy_app = create_proxy_app(app)
                public = QuietServer(
                    uvicorn.Config(
                        app,
                        uds=public_uds,
                        lifespan="on",
                        timeout_graceful_shutdown=math.ceil(settings.timeout_s),
                    )
                )
                proxy = QuietServer(uvicorn.Config(proxy_app, uds=proxy_uds, lifespan="off"))
                yield (
                    Drain(public, proxy, settings, state=app.state),
                    workload,
                    public_uds,
                    proxy_uds,
                )
        finally:
            workload.release.set()


def _client(uds: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=uds), base_url="http://chassis", timeout=10
    )


async def _started(drain: Drain) -> None:
    for _ in range(1000):
        if drain.public.started and drain.proxy.started:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the chassis never started")


async def _run_and_sigterm(drain: Drain, workload: SlowWorkload, public_uds: str) -> dict[str, Any]:
    """Start a streaming run, SIGTERM mid-run, check `/ready`, let the run finish. Returns what
    the client saw.
    """
    seen: dict[str, Any] = {}
    async with _client(public_uds) as client:
        body = {"agent": "echo", "input": {"text": "hi"}, "stream": True}
        async with client.stream("POST", "/v1/run", json=body) as stream:
            frames = stream.aiter_text()
            text = await anext(frames)
            await asyncio.to_thread(workload.waiting.wait, 10)
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(0.05)
            ready = await client.get("/ready")
            seen["ready"] = (ready.status_code, ready.json())
            seen["health"] = (await client.get("/health")).status_code
            await asyncio.sleep(drain.settings.delay_s + 0.2)  # the public listener is closing
            seen["order_at_release"] = list(drain.order)
            workload.release.set()
            text += "".join([chunk async for chunk in frames])
    seen["text"] = text
    return seen


def _events(text: str) -> list[dict[str, Any]]:
    out = []
    for frame in text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        if "data" in lines and lines.get("event") != "response":
            out.append(json.loads(lines["data"]))
    return out


async def test_the_chassis_drains_in_flight_runs_and_ready_goes_false_first() -> None:
    with pair_and_workload(DrainSettings(delay_s=0.3, timeout_s=10)) as (drain, workload, pub, _):
        served = asyncio.create_task(drain.serve())
        await _started(drain)
        seen = await _run_and_sigterm(drain, workload, pub)
        await asyncio.wait_for(served, 10)
    assert seen["ready"] == (503, {"status": "not ready", "reason": "draining"})
    assert seen["health"] == 200
    events = _events(seen["text"])
    assert events[0]["type"] == "start" and events[-1]["type"] == "end", events
    assert "".join(e.get("text", "") for e in events if e["type"] == "delta") == "half done"
    assert seen["order_at_release"] == ["draining", "public closing"]


async def test_the_proxy_listener_outlives_the_public_one() -> None:
    with pair_and_workload(DrainSettings(delay_s=0.2, timeout_s=10)) as (drain, workload, pub, _):
        served = asyncio.create_task(drain.serve())
        await _started(drain)
        await _run_and_sigterm(drain, workload, pub)
        await asyncio.wait_for(served, 10)
    assert workload.model_status == 200, "the model call after the public listener closed"
    assert workload.model_connection != "close", "only the public listener closes connections"
    assert drain.order == ["draining", "public closing", "public stopped", "proxy stopped"]
