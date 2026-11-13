"""009 CH-1: a timeout or a caller's cancel reaches the workload in every lane (P-7, decided
2026-11-13), and a JSON-RPC error answer from the workload is an `a2a.*` code (P-9).

Cancel, per lane (`inprocess`, `sidecar`, `remote` in chassis mode, and `remote` in plain mode):
the run ends with the documented terminal event, the connector sends `CancelTask`, a2a-sdk's
`on_cancel_task` cancels the producer task, so the workload sees `CancelledError` at its await,
and nothing is left running: no task, no open span. A JSON-RPC error answer is `a2a.rpc_error`
in both lanes (P-9, decided 2026-11-13).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import Any

import httpx
import pytest
from a2a.client import A2AClientError
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.helpers import new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import TaskState
from a2a.utils.constants import DEFAULT_RPC_URL
from a2a.utils.errors import InternalError
from a2a_uds import SIDECAR_URL, Recorder, serve_uds
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.connector import A2AConnector, is_jsonrpc_error_answer
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.adapters.a2a.server import BASE_URL, WireHandle, build_agent_card, build_app
from chassis.core.envelope import Budget, Request
from chassis.core.events import Error, Event, Start
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request
from fastapi import FastAPI
from poc_harness.plain_a2a import USAGE_KEY, probe_card

TIMEOUT_MS = 300
"""The run budget of the timeout cases: short, so the stalled `handle` is cut mid-await."""
SEEN_WITHIN_S = 2.0
"""How long `handle` has to see the cancel after the run ends, and the run's tasks to end."""
REMOTE_URL = "http://remote.test:8443"
"""What the remote config names. Over `uds` the host and port only fill the `Host` header."""
TOKEN_ENV = "CH1_TEST_REMOTE_TOKEN"
LANES = ["inprocess", "sidecar", "remote"]
"""Every lane the cancel box covers (P-7). `remote` here is chassis mode; plain mode below."""
PER_LOOP = frozenset({"_shutdown_watcher"})
"""Tasks that belong to the event loop, not to a run: sse-starlette starts one shutdown watcher
per loop on its first stream and keeps it."""


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
    """An `inprocess` connector on `app`: `setup` needs a dotted handle, so its transport's app
    is swapped after, the way the other `inprocess` tests do it."""
    connector = InProcessConnector()
    await connector.setup(
        {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}, _bundle(connector)
    )
    assert connector.transport is not None
    connector.transport.app = app
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


def _timed_out(events: list[Event], request: Request) -> bool:
    """Exactly the documented timeout stream: `start`, then one retryable `a2a.timeout`."""
    if len(events) != 2 or events[0] != Start(request_id=request.request_id):
        return False
    last = events[-1]
    return isinstance(last, Error) and last.code == "a2a.timeout" and last.retryable


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    value = secrets.token_hex(32)
    monkeypatch.setenv(TOKEN_ENV, value)
    return value


def _remote_config(path: str, **extra: Any) -> dict[str, Any]:
    auth = {"scheme": "bearer", "token_env": TOKEN_ENV, "previous_token_env": None}
    return {"connector": "remote", "url": REMOTE_URL, "uds": path, "auth": auth, **extra}


@asynccontextmanager
async def _connector(lane: str, app: Any, **remote: Any) -> AsyncIterator[A2AConnector]:
    """A set-up connector for `lane` on `app`. Closed by the test's `_run`."""
    if lane == "inprocess":
        yield await _inprocess_on(app)
        return
    async with serve_uds(app) as path:
        connector: A2AConnector
        if lane == "sidecar":
            connector = SidecarConnector()
            config: dict[str, Any] = {"connector": "sidecar", "url": SIDECAR_URL, "uds": path}
        else:
            connector = RemoteConnector()
            config = _remote_config(path, **remote)
        await connector.setup(config, _bundle(connector))
        yield connector


def _workload(lane: str, handle: WireHandle) -> Recorder:
    url = {"inprocess": BASE_URL, "sidecar": SIDECAR_URL}.get(lane, REMOTE_URL)
    return Recorder(build_app(handle, build_agent_card(name="t", version="1", url=url)))


def _spans(connector: A2AConnector) -> list[Any]:
    assert connector._ports is not None
    telemetry = connector._ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return list(telemetry.spans)


async def _no_task_left(before: set[asyncio.Task[Any]]) -> None:
    """Every task the run started has ended: the workload's producer, the app call, the cancel."""

    def name(task: asyncio.Task[Any]) -> str:
        coro = task.get_coro()
        return str(getattr(coro, "__qualname__", repr(coro)))

    def left() -> set[asyncio.Task[Any]]:
        new = asyncio.all_tasks() - before - {asyncio.current_task()}  # type: ignore[operator]
        return {t for t in new if name(t) not in PER_LOOP}

    try:
        async with asyncio.timeout(SEEN_WITHIN_S):
            while left():  # noqa: ASYNC110 -- no event marks the end of another task
                await asyncio.sleep(0.01)
    except TimeoutError:
        pytest.fail(f"tasks left running: {sorted(name(t) for t in left())}")


@pytest.mark.parametrize("lane", LANES)
async def test_a_timeout_cancels_the_task_and_the_workload_sees_it(lane: str, token: str) -> None:
    """P-7: in every lane the run deadline ends the run with `start` and one retryable
    `a2a.timeout`, sends `CancelTask`, and `handle` sees `CancelledError` at its await. No task
    and no span is left open."""
    handle, cancelled = _stalled()
    request = make_request().model_copy(update={"budget": Budget(timeout_ms=TIMEOUT_MS)})
    recorder = _workload(lane, handle)
    async with _connector(lane, recorder) as connector:
        before = asyncio.all_tasks()
        events = await _run(connector, request)
        async with asyncio.timeout(SEEN_WITHIN_S):
            await cancelled.wait()
        await _no_task_left(before)
    assert _timed_out(events, request), events
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]
    spans = _spans(connector)
    assert [s.name for s in spans] == ["chassis.engine.run"] and spans[0].ended
    assert spans[0].attributes["error.code"] == "a2a.timeout"
    assert spans[0].attributes["a2a.task_id"]


@pytest.mark.parametrize("lane", LANES)
async def test_a_caller_that_leaves_cancels_the_task_and_the_workload_sees_it(
    lane: str, token: str
) -> None:
    """P-7: in every lane a caller that closes the run's stream after `start` (a client
    disconnect) sends `CancelTask`, and `handle` sees `CancelledError`. No task or span is left."""
    handle, cancelled = _stalled()
    request = make_request()
    recorder = _workload(lane, handle)
    async with _connector(lane, recorder) as connector:
        before = asyncio.all_tasks()
        try:
            async with asyncio.timeout(10):
                async with aclosing(connector.run(request, make_context(request))) as run:
                    first = await anext(run)
        finally:
            await connector.close()
        async with asyncio.timeout(SEEN_WITHIN_S):
            await cancelled.wait()
        await _no_task_left(before)
    assert first == Start(request_id=request.request_id)
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]
    assert all(s.ended for s in _spans(connector))


class _StalledAgent(AgentExecutor):
    """A plain A2A agent (no chassis events): it submits the task, then works forever. Its
    `execute` sees the cancel as `CancelledError` at its await."""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()
        self.cancels: list[str] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        history = [context.message] if context.message is not None else []
        await event_queue.enqueue_event(
            new_task(
                context.task_id or "",
                context.context_id or "",
                TaskState.TASK_STATE_SUBMITTED,
                history=history,
            )
        )
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.cancels.append(context.task_id or "")
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.update_status(TaskState.TASK_STATE_CANCELED)


def _plain_app(agent: _StalledAgent) -> FastAPI:
    card = probe_card()
    handler = DefaultRequestHandler(
        agent_executor=agent, task_store=InMemoryTaskStore(), agent_card=card
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )
    return app


async def test_a_timeout_in_remote_plain_mode_cancels_and_the_agent_sees_it(token: str) -> None:
    """P-7, `remote` in plain mode: the deadline is `start` and one retryable `a2a.timeout`, the
    agent's `cancel` runs once for the task, and its `execute` sees `CancelledError`."""
    agent = _StalledAgent()
    request = make_request().model_copy(update={"budget": Budget(timeout_ms=TIMEOUT_MS)})
    recorder = Recorder(_plain_app(agent))
    plain = {"protocol": "a2a", "a2a": {"usage_key": USAGE_KEY}}
    async with _connector("remote", recorder, **plain) as connector:
        before = asyncio.all_tasks()
        events = await _run(connector, request)
        async with asyncio.timeout(SEEN_WITHIN_S):
            await agent.cancelled.wait()
        await _no_task_left(before)
    assert _timed_out(events, request), events
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]
    spans = _spans(connector)
    assert len(agent.cancels) == 1 and agent.cancels[0] == spans[0].attributes["a2a.task_id"]
    assert all(s.ended for s in spans)


def _rpc_error_app(code: int, message: str, url: str = BASE_URL) -> Any:
    """The template server's card, but every JSON-RPC call answers one SSE frame holding a
    JSON-RPC error, the way a2a-sdk's server reports an `A2AError` on a stream."""
    inner = build_app(_stalled()[0], build_agent_card(name="t", version="1", url=url))
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


async def _rpc_error_events(lane: str, code: int, message: str) -> list[Event]:
    """The events one run reads from a workload that answers a JSON-RPC error, in `lane`."""
    if lane == "inprocess":
        connector = await _inprocess_on(_rpc_error_app(code, message))
        return await _run(connector, make_request())
    async with serve_uds(_rpc_error_app(code, message, url=SIDECAR_URL)) as path:
        sidecar = SidecarConnector()
        await sidecar.setup(
            {"connector": "sidecar", "url": SIDECAR_URL, "uds": path}, _bundle(sidecar)
        )
        return await _run(sidecar, make_request())


@pytest.mark.parametrize("lane", ["inprocess", "sidecar"])
@pytest.mark.parametrize(
    ("code", "name"),
    [
        (-32603, "InternalError"),
        (-32001, "TaskNotFoundError"),
        (-32000, "ServerError"),
        (42, "CustomError"),
    ],
)
async def test_a_jsonrpc_error_answer_is_an_a2a_code(lane: str, code: int, name: str) -> None:
    """P-9: a JSON-RPC error answer ends the run with one `a2a.rpc_error`, not retryable, never
    an exception from `run` (which the pipeline would turn into `engine_error`)."""
    events = await _rpc_error_events(lane, code, f"{name} from the workload")
    assert len(events) == 1, events
    error = events[0]
    assert isinstance(error, Error), error
    assert error.code == "a2a.rpc_error" and error.retryable is False, error


def _sdk_error_for(code: int) -> Exception:
    """What a2a-sdk's JSON-RPC transport raises for an error answer with `code`."""
    transport = JsonRpcTransport.__new__(JsonRpcTransport)
    return transport._create_jsonrpc_error({"code": code, "message": "from the workload"})


@pytest.mark.parametrize("code", [-32000, -32099, 42])
def test_the_sdk_still_builds_an_unmapped_code_as_a_prefixed_client_error(code: int) -> None:
    """Pins a2a-sdk 1.2: a code outside its map is a plain `A2AClientError` whose text starts
    with `JSON-RPC Error `, the one sign `is_jsonrpc_error_answer` reads. If the SDK changes
    this, this test fails before the lane tests do."""
    exc = _sdk_error_for(code)
    assert type(exc) is A2AClientError
    assert str(exc).startswith(f"JSON-RPC Error {code}: ")
    assert is_jsonrpc_error_answer(exc)


def test_a_mapped_code_is_an_a2a_error_and_other_client_errors_are_not_answers() -> None:
    """A mapped code is its own `A2AError` subclass; an SSE or HTTP failure is not an answer."""
    assert isinstance(_sdk_error_for(-32603), InternalError)
    assert is_jsonrpc_error_answer(_sdk_error_for(-32603))
    assert not is_jsonrpc_error_answer(A2AClientError("SSE stream error: not json"))
    assert not is_jsonrpc_error_answer(A2AClientError("HTTP Error 503: unavailable"))
    assert not is_jsonrpc_error_answer(httpx.ConnectError("refused"))
