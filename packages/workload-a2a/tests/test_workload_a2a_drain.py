"""Drain on shutdown: `--drain-timeout-s` bounds how long the workload waits for in-flight
`handle` calls once it is told to stop (uvicorn's `timeout_graceful_shutdown`). PoC-4 section 6.

The mid-call test raises a real SIGTERM in this process: `DrainingServer` handles it (the server
runs on the main thread here) and does not raise it again. The PoC-4 test `test_workload_drain.py`
sends SIGTERM to a workload subprocess.
"""

from __future__ import annotations

import asyncio
import shutil
import signal
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from a2a.client import ClientConfig, ClientFactory
from workload_a2a import cli
from workload_a2a.mapping import request_to_message, update_to_event
from workload_a2a.server import (
    DEFAULT_DRAIN_TIMEOUT_S,
    DrainingServer,
    build_agent_card,
    build_server,
)

CARD = build_agent_card(name="drain", version="1", url="http://127.0.0.1:9000")
CTX: dict[str, Any] = {
    "request_id": "req-drain",
    "trace_id": "trace-drain",
    "idempotency_key": None,
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "versions": {"chassis": "0.1.0", "config": None, "prompt": None, "model_route": "r"},
    "model_route": "big-default",
}


@pytest.fixture
def uds_path() -> Iterator[str]:
    """A short path: macOS caps a Unix socket path at 104 bytes."""
    folder = Path(tempfile.mkdtemp(prefix="wd-"))
    yield str(folder / "a2a.sock")
    shutil.rmtree(folder, ignore_errors=True)


def test_build_server_sets_the_drain_timeout(uds_path: str) -> None:
    server = build_server(_slow(asyncio.Event(), 0), CARD, port=0, uds=uds_path, drain_timeout_s=7)
    assert server.config.timeout_graceful_shutdown == 7


def test_build_server_refuses_a_negative_drain_timeout(uds_path: str) -> None:
    with pytest.raises(ValueError, match="drain_timeout_s"):
        build_server(_slow(asyncio.Event(), 0), CARD, port=0, uds=uds_path, drain_timeout_s=-1)


def test_build_server_drain_timeout_defaults_to_30_s(uds_path: str) -> None:
    server = build_server(_slow(asyncio.Event(), 0), CARD, port=0, uds=uds_path)
    assert DEFAULT_DRAIN_TIMEOUT_S == 30
    assert server.config.timeout_graceful_shutdown == DEFAULT_DRAIN_TIMEOUT_S


def test_cli_passes_drain_timeout(uds_path: str) -> None:
    argv = ["serve", "--handle", "echo_python:handle", "--uds", uds_path]
    assert cli.build(argv).config.timeout_graceful_shutdown == DEFAULT_DRAIN_TIMEOUT_S
    server = cli.build([*argv, "--drain-timeout-s", "2"])
    assert server.config.timeout_graceful_shutdown == 2


def test_cli_refuses_a_negative_drain_timeout(uds_path: str) -> None:
    argv = ["serve", "--handle", "echo_python:handle", "--uds", uds_path, "--drain-timeout-s"]
    with pytest.raises(SystemExit, match="drain-timeout-s"):
        cli.build([*argv, "-1"])


def _slow(started: asyncio.Event, hold_s: float) -> Any:
    async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"type": "start", "request_id": ctx["request_id"]}
        started.set()
        await asyncio.sleep(hold_s)
        yield {"type": "delta", "text": "finished"}
        yield {"type": "end"}

    return handle


async def _call(path: str) -> list[dict[str, Any]]:
    transport = httpx.AsyncHTTPTransport(uds=path)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:9000", timeout=30
    ) as http:
        factory = ClientFactory(ClientConfig(httpx_client=http, streaming=True))
        client = await factory.create_from_url("http://127.0.0.1:9000")
        try:
            req = request_to_message({"text": "hi"}, CTX)
            events = [update_to_event(r) async for r in client.send_message(req)]
            return [e for e in events if e is not None]
        finally:
            await client.close()


async def _start(server: uvicorn.Server) -> asyncio.Task[None]:
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError("uvicorn exited before it started")
            await asyncio.sleep(0.01)
    return task


async def test_stop_mid_call_finishes_the_call(uds_path: str) -> None:
    started = asyncio.Event()
    server = build_server(_slow(started, 0.5), CARD, port=0, uds=uds_path, drain_timeout_s=10)
    serving = await _start(server)
    call = asyncio.create_task(_call(uds_path))
    async with asyncio.timeout(5):
        await started.wait()
    assert isinstance(server, DrainingServer)
    signal.raise_signal(signal.SIGTERM)
    assert server.should_exit
    async with asyncio.timeout(10):
        events = await call
        await serving
    assert [e["type"] for e in events] == ["start", "delta", "end"]
    assert events[1]["text"] == "finished"


async def test_a_call_longer_than_the_drain_timeout_is_cut(uds_path: str) -> None:
    started = asyncio.Event()
    server = build_server(_slow(started, 30), CARD, port=0, uds=uds_path, drain_timeout_s=1)
    serving = await _start(server)
    call = asyncio.create_task(_call(uds_path))
    async with asyncio.timeout(5):
        await started.wait()
    t0 = time.monotonic()
    server.should_exit = True
    async with asyncio.timeout(5):
        await serving
    assert time.monotonic() - t0 < 3
    try:
        async with asyncio.timeout(5):
            events = await call
    except Exception:  # the cut stream may surface as a client error; either way, no `end`
        events = []
    assert "end" not in [e["type"] for e in events]
