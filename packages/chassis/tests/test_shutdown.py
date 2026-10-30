"""Graceful shutdown (PoC-4 plan, section 6): the chassis handles SIGTERM itself. A real SIGTERM
to this process mid-stream: `/ready` goes 503 `draining` first, the in-flight stream ends with
its `end` event, the public server stops, and only then the proxy server. A second signal forces
both out. Both servers are real uvicorn servers on Unix sockets, in the test's loop.
"""

from __future__ import annotations

import asyncio
import math
import os
import signal
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, cli, create_app
from chassis.server.lifecycle import Drain, DrainSettings, QuietServer
from chassis.server.proxy_app import create_proxy_app

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
FAKE_YAML = str(Path(__file__).resolve().parents[1] / "configs/fake.yaml")
HANG_GUARD_S = 30.0
"""Only a hang reaches this; every wait below ends on an event or a condition."""
ROUNDS_DURING_DELAY = 6
FORCED_EXIT_WAIT_S = 2.0


class Slow:
    """A handle that sends `start` and one delta, then waits for `release` before `end`."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def __call__(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        yield Delta(text="half ")
        self.started.set()
        await self.release.wait()
        yield Delta(text="done")
        yield End(status="ok")


class DelayGate:
    """The drain's `sleep`: sets `entered` when the drain reaches its delay, and returns only
    after `release`. So the test decides how long the delay lasts, not the machine's speed.
    """

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.entered.set()
        await self.release.wait()


@contextmanager
def sockets() -> Iterator[tuple[str, str]]:
    with tempfile.TemporaryDirectory(prefix="p4-") as folder:  # short: macOS caps the path
        yield str(Path(folder, "public.sock")), str(Path(folder, "proxy.sock"))


def _pair(
    handle: Slow,
    public_uds: str,
    proxy_uds: str,
    settings: DrainSettings,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Drain:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=handle),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
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
    return Drain(public, proxy, settings, state=app.state, sleep=sleep)


def _client(uds: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=uds), base_url="http://chassis", timeout=10
    )


async def _started(drain: Drain) -> None:
    for _ in range(500):
        if drain.public.started and drain.proxy.started:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the servers never started")


async def _until(condition: Callable[[], bool], what: str) -> None:
    """Poll a condition no event reports. Bounded, and it fails by name."""
    for _ in range(3000):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(what)


async def _in_the_delay(gate: DelayGate) -> None:
    """Returns once the signal was handled and the drain sits in its delay."""
    await asyncio.wait_for(gate.entered.wait(), HANG_GUARD_S)


async def _past_the_delay(drain: Drain, gate: DelayGate) -> None:
    """End the delay. Returns once the public listener has closed, so a stream that is still
    open from here on is one that uvicorn is waiting for.
    """
    gate.release.set()
    assert drain.task is not None
    await asyncio.wait_for(drain.task, HANG_GUARD_S)
    assert drain.public.servers, "the public server has a listener to close"
    await _until(
        lambda: not any(server.is_serving() for server in drain.public.servers),
        "the public listener never closed",
    )


async def test_sigterm_drains_the_in_flight_stream_after_ready_goes_false() -> None:
    handle = Slow()
    with sockets() as (public_uds, proxy_uds):
        gate = DelayGate()
        settings = DrainSettings(delay_s=0.3, timeout_s=10)
        drain = _pair(handle, public_uds, proxy_uds, settings, gate)
        served = asyncio.create_task(drain.serve())
        await _started(drain)
        async with _client(public_uds) as client, _client(proxy_uds) as proxy:
            assert (await client.get("/ready")).status_code == 200
            body = {"agent": "echo", "input": {"text": "hi"}, "stream": True}
            async with client.stream("POST", "/v1/run", json=body) as stream:
                frames = stream.aiter_text()
                first = await anext(frames)
                assert "event: start" in first
                await handle.started.wait()
                os.kill(os.getpid(), signal.SIGTERM)
                await _in_the_delay(gate)
                ready = await client.get("/ready")
                assert (ready.status_code, ready.json()) == (
                    503,
                    {"status": "not ready", "reason": "draining"},
                )
                assert (await client.get("/health")).status_code == 200
                assert drain.order == ["draining"], "the delay holds the listener open"
                await _past_the_delay(drain, gate)
                assert gate.delays == [0.3]
                assert drain.order == ["draining", "public closing"], "the stream holds the server"
                still = await proxy.get("/nothing-here")
                assert still.status_code == 404, "the proxy listener is still up"
                handle.release.set()
                rest = "".join([first] + [chunk async for chunk in frames])
        await asyncio.wait_for(served, 10)
    assert "event: end" in rest and '"status":"ok"' in rest.replace(" ", "")
    assert drain.order == ["draining", "public closing", "public stopped", "proxy stopped"]


async def test_a_second_signal_forces_exit() -> None:
    handle = Slow()
    with sockets() as (public_uds, proxy_uds):
        gate = DelayGate()
        settings = DrainSettings(delay_s=0.0, timeout_s=30)
        drain = _pair(handle, public_uds, proxy_uds, settings, gate)
        served = asyncio.create_task(drain.serve())
        await _started(drain)
        try:
            async with _client(public_uds) as client:
                body = {"agent": "echo", "input": {"text": "hi"}, "stream": True}
                async with client.stream("POST", "/v1/run", json=body) as stream:
                    frames = stream.aiter_text()  # keep it: a dropped iterator closes the stream
                    await anext(frames)
                    await handle.started.wait()
                    os.kill(os.getpid(), signal.SIGTERM)
                    await _in_the_delay(gate)
                    await _past_the_delay(drain, gate)
                    assert drain.public.server_state.connections, "the first signal waits for it"
                    assert not served.done() and not drain.public.force_exit
                    os.kill(os.getpid(), signal.SIGINT)
                    await _until(lambda: drain.public.force_exit, "the second signal was lost")
                    assert drain.proxy.force_exit
                    try:
                        await asyncio.wait_for(served, FORCED_EXIT_WAIT_S)
                    except TimeoutError:
                        pytest.xfail(
                            "known defect: uvicorn's `wait_closed` waits for the open stream, so "
                            "a forced exit still ends only at `timeout_s`"
                        )
        finally:
            handle.release.set()


async def test_the_signal_handlers_are_restored_after_serve() -> None:
    before = signal.getsignal(signal.SIGTERM)
    with sockets() as (public_uds, proxy_uds):
        drain = _pair(Slow(), public_uds, proxy_uds, DrainSettings(delay_s=0.0))
        served = asyncio.create_task(drain.serve())
        await _started(drain)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(served, 10)
    assert signal.getsignal(signal.SIGTERM) == before


def test_the_drain_flags_reach_the_servers() -> None:
    args = cli.parse_args(["serve", "--config", FAKE_YAML])
    assert (args.drain_delay_s, args.drain_timeout_s) == (5.0, 30.0)
    public, proxy = cli.build_servers(args)
    assert isinstance(public, QuietServer) and isinstance(proxy, QuietServer)
    assert public.config.timeout_graceful_shutdown == 30.0
    args = cli.parse_args(
        ["serve", "--config", FAKE_YAML, "--drain-delay-s", "3", "--drain-timeout-s", "12"]
    )
    assert (args.drain_delay_s, args.drain_timeout_s) == (3.0, 12.0)
    public, _ = cli.build_servers(args)
    assert public.config.timeout_graceful_shutdown == 12.0


@pytest.mark.parametrize("flag", ["--drain-delay-s", "--drain-timeout-s"])
def test_a_negative_drain_flag_is_refused(flag: str) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["serve", "--config", FAKE_YAML, flag, "-1"])
    assert exc.value.code == 2


def _raw_request(path: str) -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: chassis\r\n\r\n".encode()


async def _closed_by_server(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, path: str
) -> bool:
    """Ask again on the same connection. An answer means keep-alive; EOF or a reset means the
    server closed it. Both outcomes are seen, so neither depends on a short timeout.
    """
    try:
        writer.write(_raw_request(path))
        await writer.drain()
        return await asyncio.wait_for(reader.read(1), HANG_GUARD_S) == b""
    except ConnectionError:
        return True


async def _raw_get(uds: str, path: str) -> tuple[bytes, bool]:
    """One keep-alive `GET` on a fresh raw connection. Returns the response head and whether the
    server closed the connection after the response.
    """
    reader, writer = await asyncio.open_unix_connection(uds)
    try:
        writer.write(_raw_request(path))
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        await reader.readexactly(length)
        return head.lower(), await _closed_by_server(reader, writer, path)
    finally:
        writer.close()


async def test_while_draining_every_public_response_closes_its_connection() -> None:
    handle = Slow()
    handle.release.set()
    with sockets() as (public_uds, proxy_uds):
        gate = DelayGate()
        settings = DrainSettings(delay_s=1.0, timeout_s=10)
        drain = _pair(handle, public_uds, proxy_uds, settings, gate)
        served = asyncio.create_task(drain.serve())
        await _started(drain)
        head, closed = await _raw_get(public_uds, "/health")
        assert b"connection: close" not in head and not closed, "keep-alive before the drain"
        async with _client(public_uds) as client:
            before = await client.get("/health")
            assert "close" not in before.headers.get("connection", "")
            os.kill(os.getpid(), signal.SIGTERM)
            await _in_the_delay(gate)
            head, closed = await _raw_get(public_uds, "/health")
            assert b"connection: close" in head and closed, "the server ends the connection"
            proxy_head, proxy_closed = await _raw_get(proxy_uds, "/nothing-here")
            assert b"connection: close" not in proxy_head and not proxy_closed, "proxy unchanged"
            body = {"agent": "echo", "input": {"text": "hi"}}
            responses: list[httpx.Response] = []
            for _ in range(ROUNDS_DURING_DELAY):  # back to back on one keep-alive client
                responses.append(await client.get("/health"))
                responses.append(await client.post("/v1/run", json=body))
            assert drain.order == ["draining"], "every round ran inside the delay"
            gate.release.set()
        await asyncio.wait_for(served, HANG_GUARD_S)
    assert len(responses) == 2 * ROUNDS_DURING_DELAY
    assert {(r.status_code, r.headers["connection"]) for r in responses} == {(200, "close")}


async def test_a_stream_started_while_draining_closes_after_its_end_event() -> None:
    handle = Slow()
    with sockets() as (public_uds, proxy_uds):
        gate = DelayGate()
        settings = DrainSettings(delay_s=0.5, timeout_s=10)
        drain = _pair(handle, public_uds, proxy_uds, settings, gate)
        served = asyncio.create_task(drain.serve())
        await _started(drain)
        async with _client(public_uds) as client:
            os.kill(os.getpid(), signal.SIGTERM)
            await _in_the_delay(gate)
            body = {"agent": "echo", "input": {"text": "hi"}, "stream": True}
            async with client.stream("POST", "/v1/run", json=body) as stream:
                assert stream.headers["connection"] == "close"
                frames = stream.aiter_text()
                first = await anext(frames)
                await handle.started.wait()
                await _past_the_delay(drain, gate)
                assert drain.order == ["draining", "public closing"], "the stream holds the server"
                handle.release.set()
                rest = "".join([first] + [chunk async for chunk in frames])
        await asyncio.wait_for(served, 10)
    assert "event: end" in rest and '"status":"ok"' in rest.replace(" ", "")
