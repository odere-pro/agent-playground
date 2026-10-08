"""The template A2A server a workload ships with: it serves `handle` over a Unix socket, yields
the same event stream as the chassis's own template server, maps bad events to the same error
codes, prunes its task store, and binds loopback only by default.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    CancelTaskRequest,
    ListTasksRequest,
    SendMessageRequest,
    StreamResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from fake_model_server import Script, create_app
from workload_a2a import cli
from workload_a2a.mapping import CTX_KEY, INPUT_KEY, request_to_message, update_to_event
from workload_a2a.server import (
    HandleExecutor,
    build_agent_card,
    build_app,
    build_server,
    validate_event,
)

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"

CTX: dict[str, Any] = {
    "request_id": "req-1",
    "trace_id": "trace-1",
    "idempotency_key": "idem-1",
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "versions": {"chassis": "0.1.0", "config": None, "prompt": None, "model_route": "r"},
    "model_route": "big-default",
}

StartServer = Callable[[uvicorn.Server], Awaitable[None]]


def _hi() -> SendMessageRequest:
    return request_to_message({"text": "hi"}, CTX)


CARD = build_agent_card(name="test", version="1", url="http://127.0.0.1:9000")


async def _client(http: httpx.AsyncClient) -> Client:
    factory = ClientFactory(ClientConfig(httpx_client=http, streaming=True))
    return await factory.create_from_url("http://127.0.0.1:9000")


async def _send_asgi(app: Any, req: SendMessageRequest) -> list[StreamResponse]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000") as http:
        client = await _client(http)
        try:
            return [r async for r in client.send_message(req)]
        finally:
            await client.close()


async def _send_uds(path: str, req: SendMessageRequest) -> list[StreamResponse]:
    transport = httpx.AsyncHTTPTransport(uds=path)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000") as http:
        client = await _client(http)
        try:
            return [r async for r in client.send_message(req)]
        finally:
            await client.close()


def _events(responses: list[StreamResponse]) -> list[dict[str, Any]]:
    return [e for e in (update_to_event(r) for r in responses) if e is not None]


@pytest.fixture
def echo_python() -> Iterator[Any]:
    """`echo_python:handle`, its model call routed to the fake model server. No socket."""
    workload: Any = importlib.import_module("echo_python.handle")
    workload.transport = httpx.ASGITransport(app=create_app(Script.from_yaml(EXAMPLE_SCRIPT)))
    try:
        yield workload
    finally:
        workload.transport = None


@pytest.fixture
def uds_path() -> Iterator[str]:
    """A short path: macOS caps a Unix socket path at 104 bytes, and `tmp_path` can pass that."""
    folder = Path(tempfile.mkdtemp(prefix="wa-"))
    yield str(folder / "a2a.sock")
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
async def start_server() -> AsyncIterator[StartServer]:
    running: list[tuple[uvicorn.Server, asyncio.Task[None]]] = []

    async def start(server: uvicorn.Server) -> None:
        task = asyncio.create_task(server.serve())
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("uvicorn exited before it started")
                await asyncio.sleep(0.01)
        running.append((server, task))

    yield start
    for server, task in running:
        server.should_exit = True
        await task


# --- serving over a Unix socket ---


async def test_serves_echo_python_over_a_unix_socket(
    echo_python: Any, start_server: StartServer, uds_path: str
) -> None:
    path = uds_path
    await start_server(build_server(echo_python.handle, CARD, port=9000, uds=path))
    req = request_to_message({"text": "simplify: the quick brown fox"}, CTX)
    events = _events(await _send_uds(path, req))
    assert events[0] == {"schema_version": "0", "type": "start", "request_id": "req-1"}
    assert [e["type"] for e in events[1:]].count("delta") >= 1
    assert events[-1]["type"] == "end"


async def test_same_stream_as_the_chassis_template_server(
    echo_python: Any, start_server: StartServer, uds_path: str
) -> None:
    """The workload copy over a socket and the chassis copy in memory give the same events. The
    chassis is imported only here, and only to compare; a workload-only environment skips this.
    """
    chassis_server = pytest.importorskip("chassis.adapters.a2a.server")
    path = uds_path
    await start_server(build_server(echo_python.handle, CARD, port=9000, uds=path))
    req = request_to_message({"text": "simplify: the quick brown fox"}, CTX)
    ours = _events(await _send_uds(path, req))
    theirs = _events(await _send_asgi(chassis_server.build_app(echo_python.handle, CARD), req))
    assert ours == theirs
    assert len(ours) >= 3


# --- validation and order: the same error codes as the chassis server ---


async def _minimal(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    """Defaults are filled the way `parse_event(...).model_dump()` fills them."""
    yield {"type": "start", "request_id": ctx["request_id"]}
    yield {"type": "tool_call", "call_id": "c1", "name": "glossary_lookup"}
    yield {"type": "metrics", "input_tokens": 1}
    yield {"type": "end"}


async def _future(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    yield {"schema_version": "9", "type": "delta", "text": "from the future"}


async def _unknown_type(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    yield {"schema_version": "0", "type": "thought", "text": "hmm"}


async def _extra_field(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"], "x": 1}


async def _not_a_dict(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield "start"


async def _late_start(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "delta", "text": "no start"}


async def _raising(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    raise RuntimeError("workload fell over")


async def _no_end(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}


class _Dumpable:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return dict(self._data)


async def _models(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield _Dumpable({"type": "start", "request_id": ctx["request_id"]})
    yield _Dumpable({"type": "end", "status": "ok"})


async def test_fills_defaults_like_the_chassis() -> None:
    events = _events(await _send_asgi(build_app(_minimal, CARD), _hi()))
    assert events == [
        {"schema_version": "0", "type": "start", "request_id": "req-1"},
        {
            "schema_version": "0",
            "type": "tool_call",
            "call_id": "c1",
            "name": "glossary_lookup",
            "arguments": {},
            "result": None,
        },
        {
            "schema_version": "0",
            "type": "metrics",
            "input_tokens": 1,
            "output_tokens": 0,
            "cost_usd": None,
            "model_route": None,
            "latency_ms": None,
            "attempt": 1,
        },
        {"schema_version": "0", "type": "end", "status": "ok", "output": None},
    ]


async def test_accepts_objects_with_model_dump() -> None:
    events = _events(await _send_asgi(build_app(_models, CARD), _hi()))
    assert [e["type"] for e in events] == ["start", "end"]


@pytest.mark.parametrize(
    ("handle", "code"),
    [
        (_future, "workload.bad_event"),
        (_unknown_type, "workload.bad_event"),
        (_extra_field, "workload.bad_event"),
        (_not_a_dict, "workload.bad_event"),
        (_late_start, "workload.bad_order"),
        (_raising, "workload.exception"),
        (_no_end, "workload.no_end"),
    ],
)
async def test_bad_events_map_to_the_chassis_error_codes(handle: Any, code: str) -> None:
    events = _events(await _send_asgi(build_app(handle, CARD), _hi()))
    assert events[-1]["type"] == "error" and events[-1]["code"] == code
    assert events[-1]["retryable"] is False
    assert not any(e["type"] == "end" for e in events)


async def test_bad_schema_version_names_the_version() -> None:
    events = _events(await _send_asgi(build_app(_future, CARD), _hi()))
    assert "'9'" in events[-1]["message"]


async def test_request_schema_version_is_checked() -> None:
    req = request_to_message({"text": "hi"}, CTX, schema_version="9")
    events = _events(await _send_asgi(build_app(_minimal, CARD), req))
    assert [e["code"] for e in events] == ["a2a.unsupported_schema_version"]


# --- the request is read from chassis.input and chassis.ctx (contract v1, decision 1) ---


def _recording(seen: list[tuple[dict[str, Any], dict[str, Any]]]) -> Any:
    async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        seen.append((input, ctx))
        yield {"type": "start", "request_id": ctx["request_id"]}
        yield {"type": "end"}

    return handle


async def test_input_and_ctx_reach_handle_as_sent() -> None:
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []
    req = request_to_message({"text": "hi", "data": {"n": 3, "nested": [{"k": 2}]}}, CTX)
    await _send_asgi(build_app(_recording(seen), CARD), req)
    [(input, ctx)] = seen
    assert input == {"text": "hi", "data": {"n": 3, "nested": [{"k": 2}]}}
    assert type(input["data"]["n"]) is int and type(input["data"]["nested"][0]["k"]) is int
    assert ctx == CTX and type(ctx["budget"]["max_tokens"]) is int


async def test_chassis_input_is_passed_as_is() -> None:
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []
    req = _hi()
    req.metadata[INPUT_KEY] = json.dumps({"data": {"n": 3}})
    await _send_asgi(build_app(_recording(seen), CARD), req)
    [(input, _)] = seen
    assert input == {"data": {"n": 3}}, "not reshaped to {text, data}"


async def test_falls_back_to_the_parts_and_reads_a_v0_ctx() -> None:
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []
    req = request_to_message({"text": "hi", "data": {"n": 3}}, CTX)
    del req.metadata.fields[INPUT_KEY]
    req.metadata[CTX_KEY] = CTX
    await _send_asgi(build_app(_recording(seen), CARD), req)
    [(input, ctx)] = seen
    assert input == {"text": "hi", "data": {"n": 3.0}}
    assert ctx["request_id"] == "req-1"


@pytest.mark.parametrize("key", [CTX_KEY, INPUT_KEY])
async def test_unparseable_ctx_or_input_is_a2a_bad_request(key: str) -> None:
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []
    req = _hi()
    req.metadata[key] = "{not json"
    events = _events(await _send_asgi(build_app(_recording(seen), CARD), req))
    assert [e["code"] for e in events] == ["a2a.bad_request"]
    assert key in events[0]["message"] and seen == []


async def test_a_yielded_infinity_is_workload_bad_event() -> None:
    async def inf(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"type": "start", "request_id": ctx["request_id"]}
        yield {"type": "metrics", "input_tokens": 1, "cost_usd": float("inf")}
        yield {"type": "end"}

    events = _events(await _send_asgi(build_app(inf, CARD), _hi()))
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "workload.bad_event"


def test_validate_event_is_the_one_check() -> None:
    assert validate_event({"type": "delta", "text": "x"}) == {
        "schema_version": "0",
        "type": "delta",
        "text": "x",
    }
    with pytest.raises(ValueError, match="required"):
        validate_event({"type": "delta"})


# --- the task store is pruned (PoC-1 debt, 2026-09-29) ---


async def _count(store: InMemoryTaskStore) -> int:
    page = await store.list(ListTasksRequest(), ServerCallContext())
    return int(page.total_size)


async def _until_empty(store: InMemoryTaskStore) -> int:
    for _ in range(100):
        if await _count(store) == 0:
            return 0
        await asyncio.sleep(0.01)
    return await _count(store)


async def test_store_does_not_grow_across_requests() -> None:
    store = InMemoryTaskStore()
    app = build_app(_minimal, CARD, task_store=store)
    for _ in range(5):
        events = _events(await _send_asgi(app, _hi()))
        assert events[-1]["type"] == "end"
    assert await _until_empty(store) == 0


async def test_store_is_pruned_after_errors_too() -> None:
    store = InMemoryTaskStore()
    app = build_app(_raising, CARD, task_store=store)
    for _ in range(3):
        await _send_asgi(app, _hi())
    assert await _until_empty(store) == 0


async def test_store_is_pruned_after_a_mid_run_cancel(
    start_server: StartServer, uds_path: str
) -> None:
    gate = asyncio.Event()

    async def parked(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"type": "start", "request_id": ctx["request_id"]}
        await gate.wait()
        yield {"type": "end"}

    store = InMemoryTaskStore()
    path = uds_path
    await start_server(build_server(parked, CARD, port=9000, uds=path, task_store=store))
    transport = httpx.AsyncHTTPTransport(uds=path)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000") as http:
        client = await _client(http)
        stream = client.send_message(_hi())
        task_id = ""
        async for response in stream:
            if response.HasField("task"):
                task_id = response.task.id
            if update_to_event(response) is not None:
                break
        await cast(AsyncGenerator[StreamResponse, None], stream).aclose()
        assert task_id and await _count(store) == 1
        await client.cancel_task(CancelTaskRequest(id=task_id))
        await client.close()
    gate.set()
    assert await _until_empty(store) == 0


# --- cancel while the handle is suspended at a yield ---


class _GatedQueue(EventQueue):
    """Records what the updater enqueues, and holds the first artifact update (the first `delta`)
    until `gate` is set, so the handle stays suspended at its `yield` while the executor
    publishes. `EventQueue` is abstract; the SDK owns the real one."""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.gate = asyncio.Event()
        self.held = asyncio.Event()

    async def enqueue_event(self, event: Any) -> None:
        if isinstance(event, TaskArtifactUpdateEvent) and not self.gate.is_set():
            self.held.set()
            await self.gate.wait()
        self.events.append(event)

    async def dequeue_event(self, no_wait: bool = False) -> Any:
        return self.events.pop(0)

    def task_done(self) -> None:
        pass

    async def tap(self, *args: Any, **kwargs: Any) -> Any:
        return self

    async def close(self, immediate: bool = False) -> None:
        pass

    def is_closed(self) -> bool:
        return False


@pytest.mark.parametrize("group", [False, True], ids=["plain", "cancel-scope"])
async def test_cancel_while_the_handle_is_suspended_at_a_yield_is_one_canceled(
    group: bool,
) -> None:
    """The cancel lands while the handle is parked at a `yield`, plain or holding an anyio cancel
    scope (the PydanticAI and LangGraph handles hold task groups, which hold one). The cancel does
    not close the generator from its own task; `execute` does, in the task that opened the scope.
    Exactly one terminal state, `CANCELED`, and nothing after it (no `workload.no_end`). The one
    exception is the update whose publish was already in flight when the cancel landed (`held`
    here): a publish cannot be recalled. Nothing the handle yields after the cancel is published.
    The chassis copy has the same test.
    """
    import contextlib

    import anyio

    closed: list[str] = []

    async def grouped(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        held = anyio.CancelScope() if group else contextlib.nullcontext()
        try:
            with held:
                yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
                yield {"schema_version": "0", "type": "delta", "text": "held"}
                yield {"schema_version": "0", "type": "delta", "text": "too late"}
                yield {"schema_version": "0", "type": "end", "status": "ok"}
        finally:
            closed.append("closed")

    executor = HandleExecutor(grouped)
    queue = _GatedQueue()
    context = RequestContext(
        ServerCallContext(), request=_hi(), task_id="task-1", context_id="trace-1"
    )
    run = asyncio.create_task(executor.execute(context, queue))
    await asyncio.wait_for(queue.held.wait(), timeout=1)
    await executor.cancel(context, queue)
    assert queue.events[-1].status.state == TaskState.TASK_STATE_CANCELED
    queue.gate.set()
    await asyncio.wait_for(run, timeout=1)
    terminal = {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    }
    states = [
        e.status.state
        for e in queue.events
        if isinstance(e, TaskStatusUpdateEvent) and e.status.state in terminal
    ]
    assert states == [TaskState.TASK_STATE_CANCELED]
    canceled = next(
        i
        for i, e in enumerate(queue.events)
        if isinstance(e, TaskStatusUpdateEvent) and e.status.state == TaskState.TASK_STATE_CANCELED
    )
    after = queue.events[canceled + 1 :]
    assert all(isinstance(e, TaskArtifactUpdateEvent) for e in after), after
    texts = [
        p.text
        for e in queue.events
        if isinstance(e, TaskArtifactUpdateEvent)
        for p in e.artifact.parts
    ]
    assert texts == ["held"], "nothing the handle yields after the cancel is published"
    assert closed == ["closed"], "execute closes the generator itself"


# --- localhost only by default (ADR-001) ---


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_bind(host: str) -> None:
    server = build_server(_minimal, CARD, host=host, port=9000)
    assert server.config.host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "example.com", "::"])
def test_non_loopback_host_is_refused(host: str) -> None:
    with pytest.raises(ValueError, match="ADR-001"):
        build_server(_minimal, CARD, host=host, port=9000)


def test_any_host_needs_the_explicit_flag() -> None:
    server = build_server(_minimal, CARD, host="0.0.0.0", port=9000, allow_any_host=True)
    assert server.config.host == "0.0.0.0"


def test_cli_refuses_a_non_loopback_host() -> None:
    with pytest.raises(SystemExit, match="ADR-001"):
        cli.main(["serve", "--handle", "echo_python:handle", "--port", "9000", "--host", "0.0.0.0"])


def test_cli_builds_the_server_from_a_dotted_handle() -> None:
    server = cli.build(
        ["serve", "--handle", "echo_python:handle", "--port", "9001", "--name", "echo"]
    )
    assert server.config.host == "127.0.0.1" and server.config.port == 9001


def test_cli_refuses_a_bad_handle_path() -> None:
    with pytest.raises(SystemExit, match="module:attribute"):
        cli.main(["serve", "--handle", "echo_python", "--port", "9000"])
