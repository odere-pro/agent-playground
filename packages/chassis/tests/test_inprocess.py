"""The `inprocess` connector: `load_handle` refuses a bad path, `setup` refuses a missing handle
and reads the card from config. Then the shared `A2AConnector.run` over this lane, with a stub
a2a-sdk client: each way a stream can fail is one `error` with its code, and the task is
cancelled once. A malformed event's own text stays out of the `error` and goes to the log,
redacted.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import anyio
import httpx
import pytest
from a2a.types import StreamResponse, TaskState, TaskStatusUpdateEvent
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from a2a.utils.proto_utils import to_stream_response
from chassis.adapters.a2a.inprocess import InProcessConnector, load_handle
from chassis.adapters.a2a.mapping import EVENT_KEY
from chassis.adapters.a2a.server import BASE_URL
from chassis.core.envelope import Request
from chassis.core.events import Error, Event
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request

NOT_CALLABLE = "not a handle"
STALL = object()
"""A stream item that never arrives: the read waits until the run's deadline."""
REMOTE_TEXT = "alice@example.com wrote this"
"""Text a remote puts in a malformed event; an email, so the log's redaction shows."""
SHORT_TIMEOUT_MS = 50


async def _echo(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    yield {"schema_version": "0", "type": "end", "status": "ok"}


def _bundle(connector: InProcessConnector, telemetry: InMemoryTelemetry) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(), engine=connector, config=InMemoryConfig(), telemetry=telemetry
    )


# --- load_handle and setup ---


@pytest.mark.parametrize(
    ("path", "match"),
    [
        ("json", "must be 'module:attribute'"),
        (":dumps", "must be 'module:attribute'"),
        ("json:", "must be 'module:attribute'"),
        (f"{__name__}:no_such_name", "has no attribute 'no_such_name'"),
        (f"{__name__}:NOT_CALLABLE", "is not callable"),
    ],
    ids=["no-colon", "no-module", "no-attribute-name", "missing-attribute", "not-callable"],
)
def test_load_handle_refuses_a_path_when_it_names_no_callable(path: str, match: str) -> None:
    """A path that is malformed, misses its attribute, or names a value is a `ValueError`."""
    with pytest.raises(ValueError, match=match):
        load_handle(path)


@pytest.mark.parametrize(
    "config", [{}, {"handle": ""}, {"handle": 7}], ids=["none", "empty", "int"]
)
async def test_setup_refuses_the_config_when_the_handle_is_missing(config: dict[str, Any]) -> None:
    """No `handle` string in the config is a `ValueError` that names `engine.handle`."""
    connector = InProcessConnector()

    with pytest.raises(ValueError, match=r"engine\.handle is required"):
        await connector.setup(config, _bundle(connector, InMemoryTelemetry()))


async def test_setup_serves_the_card_from_config_when_one_is_given() -> None:
    """`config["card"]` names the agent card the in-process app serves."""
    connector = InProcessConnector()
    card = {"name": "glossary", "version": "9.9.9", "description": "looks words up"}
    await connector.setup(
        {"handle": f"{__name__}:_echo", "card": card}, _bundle(connector, InMemoryTelemetry())
    )

    try:
        async with httpx.AsyncClient(transport=connector.transport, base_url=BASE_URL) as http:
            served = (await http.get(AGENT_CARD_WELL_KNOWN_PATH)).json()
    finally:
        await connector.close()

    assert (served["name"], served["version"], served["description"]) == (
        "glossary",
        "9.9.9",
        "looks words up",
    )


# --- A2AConnector.run over a stub a2a-sdk client ---


def _event(value: str) -> StreamResponse:
    update = TaskStatusUpdateEvent(
        task_id="task-1", context_id="trace-1", status={"state": TaskState.TASK_STATE_WORKING}
    )
    update.metadata[EVENT_KEY] = value
    return to_stream_response(update)


def _start(request_id: str = "req-1") -> StreamResponse:
    return _event(json.dumps({"schema_version": "0", "type": "start", "request_id": request_id}))


TASK = StreamResponse(task={"id": "task-1", "context_id": "trace-1"})


class _StubStream:
    """What `Client.send_message` returns. Items are responses, exceptions to raise, or `STALL`."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _StubStream:
        return self

    async def __anext__(self) -> StreamResponse:
        if not self._items:
            raise StopAsyncIteration
        item = self._items.pop(0)
        if item is STALL:
            await anyio.sleep_forever()
        if isinstance(item, BaseException):
            raise item
        return cast(StreamResponse, item)

    async def aclose(self) -> None:
        pass


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


async def _run(items: list[Any]) -> tuple[list[Event], _StubClient, InMemoryTelemetry]:
    connector, telemetry = InProcessConnector(), InMemoryTelemetry()
    await connector.setup({"handle": f"{__name__}:_echo"}, _bundle(connector, telemetry))
    stub = _StubClient(_StubStream(items))
    real: Any = connector._client
    connector._client = cast(Any, stub)
    base = make_request()
    request: Request = base.model_copy(
        update={"budget": base.budget.model_copy(update={"timeout_ms": SHORT_TIMEOUT_MS})}
    )
    try:
        events = [e async for e in connector.run(request, make_context(request))]
    finally:
        connector._client = real
        await connector.close()
    return events, stub, telemetry


FAILURES: list[tuple[list[Any], str, bool]] = [
    ([TASK, _start(), STALL], "a2a.timeout", True),
    ([TASK, _start(), httpx.ReadTimeout("slow")], "a2a.timeout", True),
    ([TASK, _start()], "a2a.transport", True),
    ([TASK, _start(), httpx.ConnectError("gone")], "a2a.transport", True),
    ([TASK, _start(), _event("{not json")], "a2a.bad_event", False),
    ([TASK, _start(), _event('{"schema_version": "0", "type": "nope"}')], "a2a.bad_event", False),
    ([TASK, _start("someone-else")], "a2a.request_mismatch", False),
]


@pytest.mark.parametrize(
    ("items", "code", "retryable"),
    FAILURES,
    ids=["stall", "http-timeout", "no-end", "dropped", "bad-json", "bad-schema", "mismatch"],
)
async def test_run_ends_in_one_error_and_cancels_once_when_the_stream_fails(
    items: list[Any], code: str, retryable: bool
) -> None:
    """Each failure is the run's last event, with its code and `retryable`; one cancel goes out."""
    events, stub, _ = await _run(items)

    last = events[-1]
    assert isinstance(last, Error) and (last.code, last.retryable) == (code, retryable), events
    assert [e for e in events if isinstance(e, Error)] == [last]
    assert stub.cancelled == ["task-1"]


async def test_run_records_the_transport_error_type_on_the_span_when_the_connection_drops() -> None:
    """A dropped connection names its exception type in `a2a.transport_error`."""
    _, _, telemetry = await _run([TASK, _start(), httpx.ConnectError("gone")])

    span = next(s for s in telemetry.spans if s.name == "chassis.engine.run")
    assert span.attributes["a2a.transport_error"] == "ConnectError"
    assert span.attributes["a2a.task_id"] == "task-1"


BAD_EVENTS = [
    json.dumps({"schema_version": "0", "type": "delta", "text": 5, "note": REMOTE_TEXT}),
    json.dumps([REMOTE_TEXT]),
]


@pytest.mark.parametrize("value", BAD_EVENTS, ids=["fails-schema", "not-an-object"])
async def test_run_keeps_the_remote_text_out_of_the_error_when_an_event_is_malformed(
    value: str,
) -> None:
    """The `a2a.bad_event` message is fixed text; none of the remote's words are in it."""
    events, _, _ = await _run([TASK, _start(), _event(value)])

    last = events[-1]
    assert isinstance(last, Error) and last.code == "a2a.bad_event"
    assert "alice" not in last.message and "wrote this" not in last.message


async def test_run_logs_the_detail_redacted_when_an_event_fails_the_schema() -> None:
    """The parse failure's text goes to the log, its email replaced by `<EMAIL_ADDRESS>`."""
    _, _, telemetry = await _run([TASK, _start(), _event(BAD_EVENTS[0])])

    logged = [r for r in telemetry.logs if r["message"] == "a2a bad event"]
    assert len(logged) == 1 and "<EMAIL_ADDRESS> wrote this" in logged[0]["detail"]
    assert "alice@example.com" not in json.dumps(telemetry.logs)
