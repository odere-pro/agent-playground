"""A complete call whose client hangs up is cancelled (PoC-3 debt, PoC-4 P11; plan section 5).

The app is driven over raw ASGI so the test plays the client leaving: after the body, `receive`
answers `http.disconnect` once the test says the client is gone. The watcher in `serve` sees it
within `DISCONNECT_POLL_S`, cancels the run (the workload's `handle` is cancelled), frees the
trace id, releases the idempotency claim, and counts `chassis.client_disconnected{interface}`.
No socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Event, Start
from chassis.core.trace import trace_id_hex
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.state import InMemoryState
from chassis.server import ChassisConfig, create_app
from chassis.server.idempotency import store_key
from chassis.server.interfaces import serve as serve_module
from chassis.server.pipeline import Run
from fastapi import FastAPI

AGENT = "echo"
KEY = "key-disconnect"
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": AGENT, "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
CALLS = {
    "native": ("/v1/run", {"input": {"text": "a"}, "trace_id": TRACE}),
    "openai": (
        "/v1/chat/completions",
        {"model": AGENT, "messages": [{"role": "user", "content": "a"}]},
    ),
    "anthropic": (
        "/v1/messages",
        {"model": AGENT, "max_tokens": 64, "messages": [{"role": "user", "content": "a"}]},
    ),
}


class _Hang:
    """A workload that starts and then waits forever; records that it was cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def __call__(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _app(handle: _Hang, state: InMemoryState) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=handle),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=state,
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


async def _call_and_leave(app: FastAPI, handle: _Hang, path: str, body: Any) -> list[Any]:
    """POST `body`, wait until the workload runs, then leave. Returns what the app sent."""
    raw = json.dumps(body).encode()
    gone = asyncio.Event()
    sent: list[Any] = []
    first = True

    async def receive() -> dict[str, Any]:
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": raw, "more_body": False}
        if not gone.is_set():
            await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(raw)).encode()),
        (b"idempotency-key", KEY.encode()),
        (b"traceparent", f"00-{TRACE}-00f067aa0ba902b7-01".encode()),
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("chassis", 80),
        "app": app,
    }
    call = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(handle.started.wait(), 5)
    gone.set()
    await asyncio.wait_for(call, 5)
    return sent


@pytest.mark.parametrize("interface", ["native", "openai", "anthropic"])
async def test_a_complete_call_whose_client_leaves_is_cancelled(interface: str) -> None:
    handle, state = _Hang(), InMemoryState()
    app = _app(handle, state)
    finished: list[Run] = []

    async def record(run: Run) -> None:
        finished.append(run)

    path, body = CALLS[interface]
    async with app.router.lifespan_context(app):
        app.state.pipeline.on_finished.append(record)
        await _call_and_leave(app, handle, path, body)
        assert handle.cancelled
        assert app.state.runs.lookup(trace_id_hex(TRACE)) is None
        assert await state.get(store_key(AGENT, KEY)) is None  # the claim was released
        telemetry = app.state.ports.telemetry
        assert telemetry.counter_value("chassis.client_disconnected", interface=interface) == 1
        assert [run.done for run in finished] == [False]
        # The key is free: the retry runs (and hangs again, so only check that it claims).
        handle2 = _Hang()
        app.state.ports.engine._handle = handle2
        await _call_and_leave(app, handle2, path, body)
        assert handle2.cancelled


async def test_a_check_that_fails_stops_watching_and_waits_for_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serve_module, "DISCONNECT_POLL_S", 0.01)

    async def broken() -> bool:
        raise RuntimeError("no receive")

    async def run() -> None:
        await asyncio.sleep(0.05)

    reader = asyncio.create_task(run())
    assert await serve_module._watch(reader, broken) is False
    assert reader.done()


def test_the_poll_interval_is_a_quarter_second() -> None:
    assert serve_module.DISCONNECT_POLL_S == 0.25
