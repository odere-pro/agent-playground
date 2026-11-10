"""009 CH-1, the two open boxes: a timeout cancel reaches `handle` mid-await, and a JSON-RPC
error answer from the workload is an `a2a.*` code.

The `sidecar` case passes today: a2a-sdk's `on_cancel_task` cancels the producer task after
`HandleExecutor.cancel`, so `handle` gets `CancelledError` at its await. The `inprocess` case and
the JSON-RPC cases are strict `xfail`: they wait on a contract decision (proposals.md P-7, and the
`engine_error` row of contract v1), and they fail loudly once the code changes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from a2a_uds import SIDECAR_URL, Recorder, serve_uds
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.server import BASE_URL, WireHandle, build_agent_card, build_app
from chassis.core.envelope import Budget, Request
from chassis.core.events import Error, Event
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request

TIMEOUT_MS = 300
"""The run budget of the timeout cases: short, so the stalled `handle` is cut mid-await."""
SEEN_WITHIN_S = 2.0
"""How long `handle` has to see the cancel after the run ends."""


def _bundle(engine: Any) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )


async def _run(connector: Any, request: Request) -> list[Event]:
    try:
        async with asyncio.timeout(10):
            return [e async for e in connector.run(request, make_context(request))]
    finally:
        await connector.close()


async def _inprocess_on(app: Any) -> InProcessConnector:
    """An `inprocess` connector on `app`; `setup` needs a dotted handle, so it is reopened."""
    connector = InProcessConnector()
    ports = _bundle(connector)
    await connector.setup(
        {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}, ports
    )
    await connector.close()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)
    await connector._open(client, BASE_URL, ports)
    return connector


def _stalled() -> tuple[WireHandle, asyncio.Event]:
    """A `handle` that sends `start`, then waits forever; the event is set when the wait is
    cancelled (`CancelledError` at the await, not a close at a `yield`)."""
    cancelled = asyncio.Event()

    async def stalled(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"type": "start", "request_id": ctx["request_id"]}
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        yield {"type": "end"}

    return stalled, cancelled


def _timed_out(events: list[Event]) -> bool:
    last = events[-1]
    return isinstance(last, Error) and last.code == "a2a.timeout" and last.retryable


async def test_sidecar_timeout_cancels_handle_mid_await() -> None:
    """The `sidecar` timeout sends `CancelTask`, and `handle` sees `CancelledError` at its await."""
    handle, cancelled = _stalled()
    request = make_request().model_copy(update={"budget": Budget(timeout_ms=TIMEOUT_MS)})
    recorder = Recorder(build_app(handle, build_agent_card(name="t", version="1", url=SIDECAR_URL)))
    async with serve_uds(recorder) as path:
        connector = SidecarConnector()
        await connector.setup(
            {"connector": "sidecar", "url": SIDECAR_URL, "uds": path}, _bundle(connector)
        )
        events = await _run(connector, request)
        async with asyncio.timeout(SEEN_WITHIN_S):
            await cancelled.wait()
    assert _timed_out(events)
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]


@pytest.mark.xfail(
    strict=True,
    reason="009 CH-1 box 5, proposals.md P-7: inprocess batches events and sends no cancel",
)
async def test_inprocess_timeout_cancels_handle_mid_await() -> None:
    """The `inprocess` timeout reaches `handle` too. Open: no task id reaches the connector
    before the batch, so it cannot cancel, and `handle` runs on."""
    handle, cancelled = _stalled()
    request = make_request().model_copy(update={"budget": Budget(timeout_ms=TIMEOUT_MS)})
    app = build_app(handle, build_agent_card(name="t", version="1"))
    events = await _run(await _inprocess_on(app), request)
    assert _timed_out(events)
    async with asyncio.timeout(SEEN_WITHIN_S):
        await cancelled.wait()


def _rpc_error_app(code: int, message: str) -> Any:
    """The template server's card, but every JSON-RPC call answers one SSE frame holding a
    JSON-RPC error, the way a2a-sdk's server reports an `A2AError` on a stream."""
    inner = build_app(_stalled()[0], build_agent_card(name="t", version="1"))
    frame = {"jsonrpc": "2.0", "id": "1", "error": {"code": code, "message": message}}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await inner(scope, receive, send)
            return
        headers = [(b"content-type", b"text/event-stream")]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        body = f"data: {json.dumps(frame)}\n\n".encode()
        await send({"type": "http.response.body", "body": body})

    return app


@pytest.mark.xfail(
    strict=True,
    reason="009 CH-1 box 7: a JSON-RPC error is an A2AError, not an A2AClientError, and "
    "escapes `run` as the pipeline's `engine_error` (contract v1)",
)
@pytest.mark.parametrize(
    ("code", "name"), [(-32603, "InternalError"), (-32001, "TaskNotFoundError")]
)
async def test_a_jsonrpc_error_answer_is_an_a2a_code(code: int, name: str) -> None:
    """A JSON-RPC error answer ends the run with one `a2a.*` error, never an exception."""
    connector = await _inprocess_on(_rpc_error_app(code, f"{name} from the workload"))
    events = await _run(connector, make_request())
    assert len(events) == 1
    error = events[0]
    assert isinstance(error, Error) and error.code.startswith("a2a."), error
