"""PoC-4 exit criterion 6, in the README's words:

"Stopping a pair under load fails no request in flight: the workload finishes its `handle` calls before it exits."

This file covers the workload half: a workload that gets SIGTERM mid-call finishes the call,
then exits (plan section 6). Each workload runs as a subprocess on a Unix socket, no TCP.

- `workload_a2a` (`workload-a2a serve --drain-timeout-s`) serves a handle defined in the subprocess
  itself (`--handle __main__:slow`) that holds until a file appears.
- The TypeScript echo (`node dist/src/main.js`) calls a model on another Unix socket that stalls
  mid-stream until the test lets it go.

Either way the call cannot end before the test lets it, and the test does so only once the
workload's listener has closed on SIGTERM with the process still up: the drain is seen under way.
"""  # noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from poc_harness.paths import TYPESCRIPT_ECHO
from poc_harness.scenario import CHASSIS_PROXY_URL, TYPESCRIPT_URL
from poc_harness.sockets import on_unix_socket
from poc_harness.typescript import typescript_node
from workload_a2a.mapping import request_to_message, update_to_event

CARD_URL = "http://127.0.0.1:9000"
CTX: dict[str, Any] = {
    "request_id": "req-drain",
    "trace_id": "0af7651916cd43dd8448eb211c80319c",
    "idempotency_key": None,
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "versions": {"chassis": "0.1.0", "config": None, "prompt": None, "model_route": "r"},
    "model_route": "big-default",
}
HANG_GUARD_S = 30
"""Only a hang reaches this: the test ends each stall."""

SLOW_WORKLOAD = """
import asyncio, os, pathlib
from workload_a2a.cli import main

async def slow(input, ctx):
    yield {"type": "start", "request_id": ctx["request_id"]}
    pathlib.Path(os.environ["DRAIN_MARKER"]).touch()
    release = pathlib.Path(os.environ["DRAIN_RELEASE"])
    while not release.exists():
        await asyncio.sleep(0.01)
    yield {"type": "delta", "text": "finished"}
    yield {"type": "end"}

main()
"""


@pytest.fixture
def folder() -> Iterator[Path]:
    """A short path: macOS caps a Unix socket path at 104 bytes."""
    path = Path(tempfile.mkdtemp(prefix="p4d-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _start(cmd: list[str], uds: Path, env: Mapping[str, str], cwd: Path | None = None) -> Any:
    """Start the workload; return the process once its agent card answers on `uds`."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    probe = httpx.Client(transport=httpx.HTTPTransport(uds=str(uds)), base_url=CARD_URL)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if probe.get("/.well-known/agent-card.json", timeout=1.0).status_code == 200:
                    return proc
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                raise RuntimeError(f"the workload did not start:\n{proc.communicate(timeout=5)[0]}")
            time.sleep(0.05)
    finally:
        probe.close()


def _stop(proc: Any) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


async def _call(uds: Path) -> list[dict[str, Any]]:
    transport = httpx.AsyncHTTPTransport(uds=str(uds))
    async with httpx.AsyncClient(transport=transport, base_url=CARD_URL, timeout=30) as http:
        factory = ClientFactory(ClientConfig(httpx_client=http, streaming=True))
        client = await factory.create_from_url(CARD_URL)
        try:
            req = request_to_message({"text": "simplify: drain"}, CTX)
            events = [update_to_event(r) async for r in client.send_message(req)]
            return [e for e in events if e is not None]
        finally:
            await client.close()


def _until(in_flight: Callable[[], bool], timeout_s: float = 10) -> bool:
    """Poll `in_flight` (in a worker thread) until it is true or `timeout_s` passes."""
    deadline = time.monotonic() + timeout_s
    while not in_flight():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def _listener_closed(uds: Path) -> bool:
    """True when a connect is refused. A slow answer is not a closed listener."""
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(uds)), base_url=CARD_URL) as c:
            c.get("/.well-known/agent-card.json", timeout=1.0)
    except httpx.ConnectError:
        return True
    except httpx.HTTPError:
        return False
    return False


async def _sigterm_mid_call(
    proc: Any, uds: Path, in_flight: Callable[[], bool], release: Callable[[], None]
) -> tuple[list[dict[str, Any]], int]:
    """Start a call, send SIGTERM once `in_flight()` is true, and `release()` the call once the
    drain is under way. Returns the call's events and the process's exit code.
    """
    call = asyncio.create_task(_call(uds))
    assert await asyncio.to_thread(_until, in_flight), "the call never reached the workload"
    proc.send_signal(signal.SIGTERM)
    closed = await asyncio.to_thread(_until, lambda: _listener_closed(uds))
    assert closed, "SIGTERM did not start a drain: the listener stayed open"
    assert proc.poll() is None and not call.done(), "the call ended before the drain began"
    release()
    async with asyncio.timeout(15):
        events = await call
    code = await asyncio.to_thread(proc.wait, 15)
    return events, code


async def test_workload_a2a_finishes_in_flight_handle_on_sigterm(folder: Path) -> None:
    """Exit criterion 6, workload half: the A2A template finishes its call, then exits 0."""
    uds, marker, release = folder / "a2a.sock", folder / "started", folder / "release"
    cmd = [sys.executable, "-c", SLOW_WORKLOAD, "serve", "--handle", "__main__:slow"]
    cmd += ["--uds", str(uds), "--drain-timeout-s", "10", "--log-level", "warning"]
    proc = _start(cmd, uds, {"DRAIN_MARKER": str(marker), "DRAIN_RELEASE": str(release)})
    try:
        events, code = await _sigterm_mid_call(proc, uds, marker.exists, release.touch)
    finally:
        _stop(proc)
    assert [e["type"] for e in events] == ["start", "delta", "end"], proc.stdout.read()
    assert events[1]["text"] == "finished"
    assert code == 0
    assert not uds.exists() or not _answers(uds), "the socket no longer serves"


def _answers(uds: Path) -> bool:
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(uds)), base_url=CARD_URL) as c:
            c.get("/.well-known/agent-card.json", timeout=1.0)
        return True
    except httpx.HTTPError:
        return False


class _StallingModel:
    """An OpenAI-compatible streaming model (ASGI) that sends one chunk, sets `started`, waits
    until `release` is set (at most `hold_s`, a hang guard), then sends the rest.
    """

    def __init__(self, hold_s: float) -> None:
        self.hold_s = hold_s
        self.started = threading.Event()
        self.release = threading.Event()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        while (await receive()).get("more_body"):
            pass
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": _chunk("Plain "), "more_body": True})
        self.started.set()
        await asyncio.to_thread(self.release.wait, self.hold_s)
        usage = {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}
        tail = _chunk("words.") + _sse(usage) + b"data: [DONE]\n\n"
        await send({"type": "http.response.body", "body": tail})


def _sse(obj: Any) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


def _chunk(text: str) -> bytes:
    return _sse({"choices": [{"index": 0, "delta": {"content": text}}]})


def _typescript(folder: Path, model_uds: str, drain_ms: int) -> tuple[Any, Path]:
    node = typescript_node()
    uds = folder / "ts.sock"
    env = {
        "UDS": str(uds),
        "CHASSIS_MODEL_URL": f"{CHASSIS_PROXY_URL}/v1",
        "CHASSIS_MODEL_UDS": model_uds,
        "DRAIN_TIMEOUT_MS": str(drain_ms),
    }
    assert TYPESCRIPT_URL == CARD_URL
    return _start([node, "dist/src/main.js"], uds, env, cwd=TYPESCRIPT_ECHO), uds


async def test_typescript_workload_finishes_in_flight_handle_on_sigterm(folder: Path) -> None:
    """Exit criterion 6, workload half: the TypeScript echo finishes its call, then exits 0."""
    model = _StallingModel(HANG_GUARD_S)
    with on_unix_socket(model, "model.sock") as model_uds:
        proc, uds = _typescript(folder, model_uds, drain_ms=10_000)
        try:
            events, code = await _sigterm_mid_call(
                proc, uds, model.started.is_set, model.release.set
            )
        finally:
            _stop(proc)
    types = [e["type"] for e in events]
    assert types[0] == "start" and types[-1] == "end", (types, proc.stdout.read())
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Plain words."
    assert code == 0


async def test_typescript_workload_exits_1_when_a_call_outlives_the_drain_timeout(
    folder: Path,
) -> None:
    """Exit criterion 6, its limit: past the drain timeout the TypeScript echo exits 1."""
    model = _StallingModel(HANG_GUARD_S)
    with on_unix_socket(model, "model.sock") as model_uds:
        proc, uds = _typescript(folder, model_uds, drain_ms=300)
        try:
            call = asyncio.create_task(_call(uds))
            assert await asyncio.to_thread(model.started.wait, 10)
            t0 = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            code = await asyncio.to_thread(proc.wait, 10)
            elapsed = time.monotonic() - t0
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
        finally:
            model.release.set()
            _stop(proc)
    assert code == 1
    assert elapsed < 5
