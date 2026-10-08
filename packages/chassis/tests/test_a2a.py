"""The A2A mapping and the template server: every chassis event is one A2A update and back, the
messages validate against the a2a-sdk types, `context_id` is the trace id, and a bad event from
the workload becomes one `error` on the wire.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, get_message_text
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCard,
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.proto_utils import to_stream_response
from chassis.adapters.a2a.inprocess import InProcessConnector
from chassis.adapters.a2a.mapping import (
    CTX_KEY,
    EVENT_KEY,
    OUTPUT_ARTIFACT_ID,
    SCHEMA_VERSION_KEY,
    event_to_update,
    request_to_message,
    update_to_event,
)
from chassis.adapters.a2a.server import HandleExecutor, build_agent_card, build_app
from chassis.core.events import Delta, End, Error, Event, Metrics, Start, ToolCall, parse_event
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request
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
    assert get_data_parts(wire.message.parts) == [{"k": 1.0}]
    meta = json_format.MessageToDict(wire.metadata)
    assert meta[SCHEMA_VERSION_KEY] == "0"
    assert meta[CTX_KEY]["request_id"] == "req-1" and meta[CTX_KEY]["model_route"] == "r"
    assert meta[CTX_KEY]["versions"]["config"] is None


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
    from a2a.server.agent_execution import AgentExecutor

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


# A `budget.timeout_ms` test is not here on purpose: `httpx.ASGITransport` runs the whole app
# call before it returns the body, so no timeout can fire mid-run in this lane and the test could
# not be made deterministic. See contract-v0.md, "`EngineConnector`, draft".
