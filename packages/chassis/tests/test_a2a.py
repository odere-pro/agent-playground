"""The A2A mapping and the template server: every chassis event is one A2A update and back, the
messages validate against the a2a-sdk types, `context_id` is the trace id, and a bad event from
the workload becomes one `error` on the wire.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, get_message_text, new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCard,
    ListTasksRequest,
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import DEFAULT_RPC_URL
from a2a.utils.proto_utils import to_stream_response
from chassis.adapters.a2a.inprocess import InProcessConnector
from chassis.adapters.a2a.mapping import (
    CTX_KEY,
    EVENT_KEY,
    INPUT_KEY,
    OUTPUT_ARTIFACT_ID,
    SCHEMA_VERSION_KEY,
    BadJson,
    event_to_update,
    request_to_message,
    update_to_event,
)
from chassis.adapters.a2a.server import HandleExecutor, build_agent_card, build_app
from chassis.core.events import Delta, End, Error, Event, Metrics, Start, ToolCall, parse_event
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request
from fastapi import FastAPI
from google.protobuf import json_format

EVENTS: list[Event] = [
    Start(request_id="req-1"),
    Delta(text="plain "),
    ToolCall(call_id="c1", name="glossary_lookup", arguments={"term": "SLM"}, result={"n": 2}),
    Metrics(input_tokens=12, output_tokens=3, cost_usd=0.5, model_route="big-default"),
    End(status="fallback", output={"text": "done"}),
    Error(code="upstream", message="boom", retryable=True),
]

CTX: dict[str, Any] = {
    "request_id": "req-1",
    "trace_id": "trace-1",
    "idempotency_key": "idem-1",
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "versions": {"chassis": "0.1.0", "config": None, "prompt": None, "model_route": "r"},
    "model_route": "r",
}


class _Queue(EventQueue):
    """Records what the updater enqueues. `EventQueue` is abstract; the SDK owns the real one."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
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


def _roundtrip(wire: StreamResponse) -> StreamResponse:
    """Serialize to JSON and parse back, the way the JSON-RPC binding does."""
    parsed = StreamResponse()
    json_format.ParseDict(json_format.MessageToDict(wire), parsed)
    return parsed


@pytest.mark.parametrize("event", EVENTS, ids=[e.type for e in EVENTS])
async def test_every_event_round_trips_through_one_a2a_update(event: Event) -> None:
    queue = _Queue()
    updater = TaskUpdater(queue, "task-1", "trace-1")
    await event_to_update(event.model_dump(mode="json"), updater)
    assert len(queue.events) == 1, "one chassis event is exactly one A2A event"
    wire = _roundtrip(to_stream_response(queue.events[0]))
    raw = update_to_event(wire)
    assert raw is not None
    assert parse_event(raw) == event


async def test_states_and_native_parts_follow_the_table() -> None:
    queue = _Queue()
    updater = TaskUpdater(queue, "task-1", "trace-1")
    for event in EVENTS[:-1]:
        await event_to_update(event.model_dump(mode="json"), updater)
    start, delta, tool, metrics, end = queue.events
    assert isinstance(start, TaskStatusUpdateEvent)
    assert start.status.state == TaskState.TASK_STATE_WORKING and not start.status.HasField(
        "message"
    )
    assert isinstance(delta, TaskArtifactUpdateEvent)
    assert delta.artifact.artifact_id == OUTPUT_ARTIFACT_ID and delta.append is False
    assert get_message_text(tool.status.message) == "" and get_data_parts(
        tool.status.message.parts
    ) == [
        {
            "call_id": "c1",
            "name": "glossary_lookup",
            "arguments": {"term": "SLM"},
            "result": {"n": 2.0},
        }
    ]
    assert metrics.status.state == TaskState.TASK_STATE_WORKING
    assert end.status.state == TaskState.TASK_STATE_COMPLETED
    assert get_data_parts(end.status.message.parts) == [{"text": "done"}]
    assert all(e.context_id == "trace-1" for e in queue.events)

    error = Error(code="x", message="fell over")
    updater = TaskUpdater(queue, "task-2", "trace-1")  # `end` was terminal for task-1
    await event_to_update(error.model_dump(mode="json"), updater)
    failed = queue.events[-1]
    assert failed.status.state == TaskState.TASK_STATE_FAILED
    assert get_message_text(failed.status.message) == "fell over"


async def test_second_delta_appends() -> None:
    queue = _Queue()
    updater = TaskUpdater(queue, "task-1", "trace-1")
    await event_to_update(Delta(text="a").model_dump(mode="json"), updater)
    await event_to_update(Delta(text="b").model_dump(mode="json"), updater, append=True)
    assert [e.append for e in queue.events] == [False, True]


def test_request_to_message_puts_ctx_in_metadata_and_trace_in_context_id() -> None:
    req = request_to_message({"text": "hello", "data": {"k": 1}}, CTX)
    assert isinstance(req, SendMessageRequest)
    wire = json_format.ParseDict(json_format.MessageToDict(req), SendMessageRequest())
    assert wire.message.role == Role.ROLE_USER
    assert wire.message.context_id == CTX["trace_id"]
    assert len(wire.message.message_id) == 32
    assert get_message_text(wire.message) == "hello"
    assert get_data_parts(wire.message.parts) == [{"k": 1.0}], "native parts stay the v0 view"
    meta = json_format.MessageToDict(wire.metadata)
    assert meta[SCHEMA_VERSION_KEY] == "0"
    ctx = json.loads(meta[CTX_KEY])
    assert ctx == CTX and type(ctx["budget"]["max_tokens"]) is int
    assert ctx["versions"]["config"] is None


def test_chassis_json_crosses_as_a_compact_string() -> None:
    """Decision 1: `chassis.ctx`, `chassis.input`, and `chassis.event` are JSON strings written
    with `separators=(",", ":")`; the reader gets back exactly what the writer wrote.
    """
    input = {"text": "hello", "data": {"k": 1, "f": 1.5}}
    meta = json_format.MessageToDict(request_to_message(input, CTX).metadata)
    assert meta[CTX_KEY] == json.dumps(CTX, separators=(",", ":"))
    assert meta[INPUT_KEY] == json.dumps(input, separators=(",", ":"))


async def test_event_metadata_is_a_json_string() -> None:
    queue = _Queue()
    updater = TaskUpdater(queue, "task-1", "trace-1")
    for event in EVENTS[:3]:
        await event_to_update(event.model_dump(mode="json"), updater)
    start, delta, tool = (to_stream_response(e) for e in queue.events)
    for meta in (
        json_format.MessageToDict(start.status_update.metadata),
        json_format.MessageToDict(delta.artifact_update.artifact.metadata),
        json_format.MessageToDict(tool.status_update.metadata),
    ):
        assert isinstance(meta[EVENT_KEY], str)
    assert json.loads(json_format.MessageToDict(tool.status_update.metadata)[EVENT_KEY])[
        "result"
    ] == {"n": 2}


async def test_an_event_that_is_not_json_is_refused_before_it_is_published() -> None:
    queue = _Queue()
    updater = TaskUpdater(queue, "task-1", "trace-1")
    with pytest.raises(ValueError):
        await event_to_update({"type": "delta", "text": "x", "nan": float("nan")}, updater)
    assert queue.events == []


def _status_with(value: Any) -> StreamResponse:
    update = TaskStatusUpdateEvent(
        task_id="t", context_id="c", status={"state": TaskState.TASK_STATE_WORKING}
    )
    update.metadata[EVENT_KEY] = value
    return _roundtrip(to_stream_response(update))


def test_a_v0_struct_event_is_still_read() -> None:
    """v0 wrote `chassis.event` as a `Struct`; readers accept it through v1, doubles and all."""
    v0 = Metrics(input_tokens=12, output_tokens=3, model_route="r").model_dump(mode="json")
    raw = update_to_event(_status_with(v0))
    assert raw is not None and raw["input_tokens"] == 12.0
    assert parse_event(raw) == Metrics(input_tokens=12, output_tokens=3, model_route="r")


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '"a string"', "3"])
def test_a_string_that_is_not_a_json_object_is_bad_json(bad: str) -> None:
    with pytest.raises(BadJson, match=EVENT_KEY):
        update_to_event(_status_with(bad))


def test_empty_input_sends_no_text_part() -> None:
    req = request_to_message({"text": None, "data": {}}, CTX)
    assert get_message_text(req.message) == ""
    assert get_data_parts(req.message.parts) == []


def test_a2a_states_without_a_chassis_event() -> None:
    def status(state: int) -> StreamResponse:
        return to_stream_response(
            TaskStatusUpdateEvent(task_id="t", context_id="c", status={"state": state})
        )

    assert update_to_event(status(TaskState.TASK_STATE_COMPLETED)) == {
        "schema_version": "0",
        "type": "end",
        "status": "ok",
    }
    failed = update_to_event(status(TaskState.TASK_STATE_FAILED))
    assert failed is not None and failed["code"] == "a2a.failed"
    canceled = update_to_event(status(TaskState.TASK_STATE_CANCELED))
    assert canceled is not None and canceled["code"] == "a2a.canceled"
    assert update_to_event(status(TaskState.TASK_STATE_CANCELED), cancelled=True) is None
    auth = update_to_event(status(TaskState.TASK_STATE_AUTH_REQUIRED))
    assert auth is not None and auth["code"] == "a2a.unsupported_state"
    assert update_to_event(status(TaskState.TASK_STATE_WORKING)) is None
    assert update_to_event(StreamResponse(task={"id": "t", "context_id": "c"})) is None


# --- the template server ---


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://inprocess")


async def _send(app: Any, req: SendMessageRequest) -> list[StreamResponse]:
    async with _client(app) as http:
        client = await ClientFactory(
            ClientConfig(httpx_client=http, streaming=True)
        ).create_from_url("http://inprocess")
        try:
            return [r async for r in client.send_message(req)]
        finally:
            await client.close()


def _events(responses: list[StreamResponse]) -> list[Event]:
    out: list[Event] = []
    for r in responses:
        raw = update_to_event(r)
        if raw is not None:
            out.append(parse_event(raw))
    return out


async def _dict_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    yield {"schema_version": "0", "type": "delta", "text": f"got {input.get('text')}"}
    yield {"schema_version": "0", "type": "metrics", "input_tokens": 1, "output_tokens": 2}
    yield {"schema_version": "0", "type": "end", "status": "ok"}


async def _model_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Event]:
    yield Start(request_id=ctx["request_id"])
    yield Delta(text="typed")
    yield End()


async def _future_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    yield {"schema_version": "9", "type": "delta", "text": "from the future"}
    yield {"schema_version": "0", "type": "end", "status": "ok"}


async def _late_start(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "delta", "text": "no start"}


async def _raising(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    raise RuntimeError("workload fell over")


async def _no_end(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}


def test_agent_card_is_a_valid_a2a_card() -> None:
    card = build_agent_card(name="echo_python:handle", version="0.1.0", description="d")
    assert isinstance(card, AgentCard)
    parsed = json_format.ParseDict(json_format.MessageToDict(card), AgentCard())
    assert parsed.name == "echo_python:handle" and parsed.capabilities.streaming
    assert parsed.supported_interfaces[0].protocol_binding == "JSONRPC"


async def test_server_streams_dict_events_over_a2a() -> None:
    app = build_app(_dict_handle, build_agent_card(name="dict", version="1"))
    responses = await _send(app, request_to_message({"text": "hi"}, CTX))
    assert responses[0].HasField("task"), "a2a-sdk 1.2 needs the task before the first update"
    assert all(r.task.context_id == "trace-1" for r in responses if r.HasField("task"))
    assert all(
        r.status_update.context_id == "trace-1" for r in responses if r.HasField("status_update")
    )
    assert _events(responses) == [
        Start(request_id="req-1"),
        Delta(text="got hi"),
        Metrics(input_tokens=1, output_tokens=2),
        End(),
    ]


async def test_server_accepts_pydantic_events_too() -> None:
    app = build_app(_model_handle, build_agent_card(name="typed", version="1"))
    responses = await _send(app, request_to_message({"text": "hi"}, CTX))
    assert _events(responses) == [Start(request_id="req-1"), Delta(text="typed"), End()]


async def test_unsupported_schema_version_becomes_an_error_event() -> None:
    app = build_app(_future_handle, build_agent_card(name="future", version="1"))
    events = _events(await _send(app, request_to_message({"text": "hi"}, CTX)))
    assert isinstance(events[-1], Error)
    assert events[-1].code == "workload.bad_event" and "9" in events[-1].message
    assert [e.type for e in events] == ["start", "error"]


@pytest.mark.parametrize(
    ("handle", "code"),
    [
        (_late_start, "workload.bad_order"),
        (_raising, "workload.exception"),
        (_no_end, "workload.no_end"),
    ],
)
async def test_server_keeps_the_order_the_port_promises(handle: Any, code: str) -> None:
    app = build_app(handle, build_agent_card(name="bad", version="1"))
    events = _events(await _send(app, request_to_message({"text": "hi"}, CTX)))
    assert isinstance(events[-1], Error) and events[-1].code == code
    assert events.count(events[-1]) == 1 and not any(isinstance(e, End) for e in events)


async def test_request_schema_version_is_checked() -> None:
    app = build_app(_dict_handle, build_agent_card(name="dict", version="1"))
    req = request_to_message({"text": "hi"}, CTX, schema_version="9")
    events = _events(await _send(app, req))
    assert [e.type for e in events] == ["error"]
    assert isinstance(events[0], Error) and events[0].code == "a2a.unsupported_schema_version"


def test_executor_is_an_agent_executor() -> None:
    assert issubclass(HandleExecutor, AgentExecutor)
    assert EVENT_KEY == "chassis.event" and CTX_KEY == "chassis.ctx"


async def test_cancel_while_the_handle_is_mid_await_is_clean() -> None:
    """`aclose()` on a running generator raises `RuntimeError`. A cancel that lands while the
    handle awaits sets a flag instead; the executor stops at the next event and publishes nothing
    after `TASK_STATE_CANCELED`.
    """
    gate = asyncio.Event()

    async def blocking(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
        await gate.wait()
        yield {"schema_version": "0", "type": "delta", "text": "too late"}
        yield {"schema_version": "0", "type": "end", "status": "ok"}

    executor = HandleExecutor(blocking)
    queue = _Queue()
    context = RequestContext(
        ServerCallContext(),
        request=request_to_message({"text": "hi"}, CTX),
        task_id="task-1",
        context_id="trace-1",
    )
    run = asyncio.create_task(executor.execute(context, queue))
    for _ in range(20):
        await asyncio.sleep(0)
        if any(isinstance(e, TaskStatusUpdateEvent) for e in queue.events):
            break
    assert not run.done(), "the handle must be parked on its await"
    await executor.cancel(context, queue)
    assert queue.events[-1].status.state == TaskState.TASK_STATE_CANCELED
    gate.set()
    await asyncio.wait_for(run, timeout=1)
    assert queue.events[-1].status.state == TaskState.TASK_STATE_CANCELED
    assert not any(isinstance(e, TaskArtifactUpdateEvent) for e in queue.events)


class _GatedQueue(_Queue):
    """Holds the first artifact update (the first `delta`) until `gate` is set, so the handle
    stays suspended at its `yield` while the executor publishes."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.held = asyncio.Event()

    async def enqueue_event(self, event: Any) -> None:
        if isinstance(event, TaskArtifactUpdateEvent) and not self.gate.is_set():
            self.held.set()
            await self.gate.wait()
        await super().enqueue_event(event)


def _terminal_states(events: list[Any]) -> list[int]:
    done = {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
    }
    return [
        e.status.state
        for e in events
        if isinstance(e, TaskStatusUpdateEvent) and e.status.state in done
    ]


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
        ServerCallContext(),
        request=request_to_message({"text": "hi"}, CTX),
        task_id="task-1",
        context_id="trace-1",
    )
    run = asyncio.create_task(executor.execute(context, queue))
    await asyncio.wait_for(queue.held.wait(), timeout=1)
    await executor.cancel(context, queue)
    assert queue.events[-1].status.state == TaskState.TASK_STATE_CANCELED
    queue.gate.set()
    await asyncio.wait_for(run, timeout=1)
    assert _terminal_states(queue.events) == [TaskState.TASK_STATE_CANCELED]
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


# --- the server reads chassis.input and chassis.ctx (decision 1) ---


def _recording(seen: list[tuple[dict[str, Any], dict[str, Any]]]) -> Any:
    async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        seen.append((input, ctx))
        yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
        yield {"schema_version": "0", "type": "end", "status": "ok"}

    return handle


async def test_server_passes_chassis_input_and_ctx_as_is() -> None:
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []
    app = build_app(_recording(seen), build_agent_card(name="rec", version="1"))
    req = request_to_message({"text": "hi", "data": {"n": 3}}, CTX)
    req.metadata[INPUT_KEY] = json.dumps({"data": {"n": 3}, "extra": [1]})
    await _send(app, req)
    [(input, ctx)] = seen
    assert input == {"data": {"n": 3}, "extra": [1]}, "not reshaped to {text, data}"
    assert type(input["data"]["n"]) is int
    assert ctx == CTX and type(ctx["budget"]["max_tokens"]) is int


async def test_server_falls_back_to_the_parts_without_chassis_input() -> None:
    """A generic A2A client sends no `chassis.input`; a v0 `Struct` `chassis.ctx` is still read."""
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []
    app = build_app(_recording(seen), build_agent_card(name="rec", version="1"))
    req = request_to_message({"text": "hi", "data": {"n": 3}}, CTX)
    del req.metadata.fields[INPUT_KEY]
    req.metadata[CTX_KEY] = CTX
    await _send(app, req)
    [(input, ctx)] = seen
    assert input == {"text": "hi", "data": {"n": 3.0}}
    assert ctx["request_id"] == "req-1" and ctx["budget"]["max_tokens"] == 2000


@pytest.mark.parametrize("key", [CTX_KEY, INPUT_KEY])
@pytest.mark.parametrize("bad", ["{not json", "[1]"])
async def test_unparseable_ctx_or_input_is_a2a_bad_request(key: str, bad: str) -> None:
    seen: list[tuple[dict[str, Any], dict[str, Any]]] = []
    app = build_app(_recording(seen), build_agent_card(name="rec", version="1"))
    req = request_to_message({"text": "hi"}, CTX)
    req.metadata[key] = bad
    responses = await _send(app, req)
    events = _events(responses)
    assert [e.type for e in events] == ["error"]
    assert isinstance(events[0], Error) and events[0].code == "a2a.bad_request"
    assert key in events[0].message and not events[0].retryable
    assert seen == [], "handle must not run"
    last = responses[-1]
    assert last.status_update.status.state == TaskState.TASK_STATE_FAILED


async def test_schema_version_is_checked_before_the_request_is_read() -> None:
    app = build_app(_dict_handle, build_agent_card(name="dict", version="1"))
    req = request_to_message({"text": "hi"}, CTX, schema_version="9")
    req.metadata[CTX_KEY] = "{not json"
    events = _events(await _send(app, req))
    assert [e.code for e in events if isinstance(e, Error)] == ["a2a.unsupported_schema_version"]


async def test_a_yielded_nan_is_workload_bad_event() -> None:
    async def nan(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
        yield {"type": "tool_call", "call_id": "c", "name": "n", "arguments": {"x": float("inf")}}
        yield {"schema_version": "0", "type": "end", "status": "ok"}

    app = build_app(nan, build_agent_card(name="nan", version="1"))
    events = _events(await _send(app, request_to_message({"text": "hi"}, CTX)))
    assert [e.type for e in events] == ["start", "error"]
    assert isinstance(events[-1], Error) and events[-1].code == "workload.bad_event"


# --- the chassis server prunes its task store, like the workload copy ---


async def test_chassis_server_prunes_terminal_tasks() -> None:
    store = InMemoryTaskStore()
    app = build_app(_dict_handle, build_agent_card(name="dict", version="1"), task_store=store)
    for _ in range(3):
        await _send(app, request_to_message({"text": "hi"}, CTX))
    for _ in range(100):
        page = await store.list(ListTasksRequest(), ServerCallContext())
        if page.total_size == 0:
            break
        await asyncio.sleep(0.01)
    assert page.total_size == 0


# --- the inprocess connector ---


async def _foreign_start(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": "someone-else"}
    yield {"schema_version": "0", "type": "end", "status": "ok"}


async def test_connector_refuses_a_start_for_another_request() -> None:
    connector = InProcessConnector()
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    await connector.setup(
        {"connector": "inprocess", "handle": f"{__name__}:_foreign_start"}, bundle
    )
    request = make_request()
    try:
        events = [e async for e in connector.run(request, make_context(request))]
    finally:
        await connector.close()
    assert len(events) == 1 and isinstance(events[0], Error)
    assert events[0].code == "a2a.request_mismatch"
    assert "someone-else" in events[0].message and request.request_id in events[0].message


class _RawExecutor(AgentExecutor):
    """Publishes `chassis.event` metadata values as given: v0 `Struct`s or bad strings."""

    def __init__(self, values: list[Any]) -> None:
        self._values = values

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id, context_id = context.task_id or "", context.context_id or ""
        await event_queue.enqueue_event(
            new_task(task_id, context_id, TaskState.TASK_STATE_SUBMITTED)
        )
        updater = TaskUpdater(event_queue, task_id, context_id)
        for value in self._values:
            await updater.update_status(TaskState.TASK_STATE_WORKING, metadata={EVENT_KEY: value})
        await updater.update_status(TaskState.TASK_STATE_COMPLETED)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def _raw_app(values: list[Any]) -> FastAPI:
    card = build_agent_card(name="raw", version="1")
    handler = DefaultRequestHandler(
        agent_executor=_RawExecutor(values), task_store=InMemoryTaskStore(), agent_card=card
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )
    return app


async def _run_raw(values: list[Any]) -> list[Event]:
    connector = InProcessConnector()
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    await connector.setup({"connector": "inprocess", "handle": f"{__name__}:_dict_handle"}, bundle)
    transport: Any = connector.transport
    transport.app = _raw_app(values)
    request = make_request()
    try:
        return [e async for e in connector.run(request, make_context(request))]
    finally:
        await connector.close()


async def test_connector_reads_a_v0_struct_event_stream() -> None:
    events = await _run_raw(
        [
            {"schema_version": "0", "type": "start", "request_id": "req-1"},
            {"schema_version": "0", "type": "metrics", "input_tokens": 5, "output_tokens": 1},
            {"schema_version": "0", "type": "end", "status": "ok"},
        ]
    )
    assert events == [
        Start(request_id="req-1"),
        Metrics(input_tokens=5, output_tokens=1),
        End(),
    ]


async def test_connector_turns_an_unparseable_event_string_into_a2a_bad_event() -> None:
    events = await _run_raw([json.dumps({"type": "start", "request_id": "req-1"}), "{not json"])
    assert [e.type for e in events] == ["start", "error"]
    assert isinstance(events[-1], Error) and events[-1].code == "a2a.bad_event"


# The run deadline (`budget.timeout_ms` for the whole run) is a `LaneContract` case, bound for
# this lane in `test_lane_contract.py`.


# --- the connector against a stub a2a-sdk client: early end, a closer that raises ---


def _start_response(request_id: str) -> StreamResponse:
    update = TaskStatusUpdateEvent(
        task_id="task-1", context_id="trace-1", status={"state": TaskState.TASK_STATE_WORKING}
    )
    update.metadata[EVENT_KEY] = json.dumps(
        {"schema_version": "0", "type": "start", "request_id": request_id}
    )
    return to_stream_response(update)


class _StubStream:
    """What `Client.send_message` returns: the task, `start`, then the end of the stream."""

    def __init__(self, responses: list[StreamResponse], *, close_raises: bool = False) -> None:
        self._responses = list(responses)
        self._close_raises = close_raises
        self.closed = False

    def __aiter__(self) -> _StubStream:
        return self

    async def __anext__(self) -> StreamResponse:
        if not self._responses:
            raise StopAsyncIteration
        return self._responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True
        if self._close_raises:
            raise RuntimeError("closer fell over")


class _StubClient:
    def __init__(self, stream: _StubStream) -> None:
        self.stream = stream
        self.cancelled: list[str] = []

    def send_message(self, message: Any, *, context: Any = None) -> _StubStream:
        return self.stream

    async def cancel_task(self, request: Any, *, context: Any = None) -> None:
        self.cancelled.append(request.id)

    async def close(self) -> None:
        pass


async def _run_stub(stream: _StubStream) -> tuple[list[Event], _StubClient, InMemoryTelemetry]:
    connector = InProcessConnector()
    telemetry = InMemoryTelemetry()
    bundle = PortBundle(
        model=ScriptedModel(), engine=connector, config=InMemoryConfig(), telemetry=telemetry
    )
    await connector.setup({"connector": "inprocess", "handle": f"{__name__}:_dict_handle"}, bundle)
    stub = _StubClient(stream)
    request = make_request()
    real: Any = connector._client
    connector._client = cast(Any, stub)
    try:
        events = [e async for e in connector.run(request, make_context(request))]
    finally:
        connector._client = real
        await connector.close()
    return events, stub, telemetry


def _task_and_start() -> list[StreamResponse]:
    return [
        StreamResponse(task={"id": "task-1", "context_id": "trace-1"}),
        _start_response(make_request().request_id),
    ]


async def test_a_stream_that_ends_without_end_or_error_is_a2a_transport() -> None:
    stream = _StubStream(_task_and_start())
    events, stub, _ = await _run_stub(stream)
    assert [e.type for e in events] == ["start", "error"]
    error = events[-1]
    assert isinstance(error, Error) and error.code == "a2a.transport" and error.retryable
    assert error.message == "stream ended without end or error"
    assert stream.closed and stub.cancelled == ["task-1"], "the task the server left is cancelled"


async def test_a_closer_that_raises_still_cancels_and_is_logged() -> None:
    stream = _StubStream(_task_and_start(), close_raises=True)
    events, stub, telemetry = await _run_stub(stream)
    assert [e.type for e in events] == ["start", "error"]
    assert stub.cancelled == ["task-1"], "the cancel is sent even when the close fails"
    logged = [r for r in telemetry.logs if "close" in r["message"]]
    assert logged and "closer fell over" in str(logged[0].get("reason")), telemetry.logs
