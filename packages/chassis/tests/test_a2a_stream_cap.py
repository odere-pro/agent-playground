"""The connector's limits on what a lane's A2A stream may send (contract v8; R2-11, R1-09).

- A stream past `STREAM_EVENTS_MAX` events or `STREAM_BYTES_MAX` bytes ends the run with one
  `a2a.stream_too_large` error (not retryable) and cancels the remote's task.
- An event outside the v0 bounds (a negative token count) is `a2a.bad_event`.
- The text of a transport failure is cut to `ERROR_MESSAGE_MAX`.

Driven against a stub a2a-sdk client, no socket.
"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest
from a2a.types import StreamResponse, TaskState, TaskStatusUpdateEvent
from a2a.utils.proto_utils import to_stream_response
from chassis.adapters.a2a import connector as connector_module
from chassis.adapters.a2a.inprocess import InProcessConnector
from chassis.adapters.a2a.mapping import EVENT_KEY
from chassis.core.events import ERROR_MESSAGE_MAX, Error, Event
from chassis.core.inbound import status_for
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request

REQUEST = make_request()


def _event(raw: dict[str, Any]) -> StreamResponse:
    update = TaskStatusUpdateEvent(
        task_id="task-1", context_id="trace-1", status={"state": TaskState.TASK_STATE_WORKING}
    )
    update.metadata[EVENT_KEY] = json.dumps({"schema_version": "0", **raw})
    return to_stream_response(update)


def _task() -> StreamResponse:
    return StreamResponse(task={"id": "task-1", "context_id": "trace-1"})


class _Stream:
    """What `Client.send_message` returns: the given items, then `tail()` forever or the end."""

    def __init__(self, head: list[StreamResponse], tail: Any = None, raises: Any = None) -> None:
        self._head = list(head)
        self._tail = tail
        self._raises = raises
        self.read = 0
        self.closed = False

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> StreamResponse:
        self.read += 1
        if self._head:
            return self._head.pop(0)
        if self._raises is not None:
            raise self._raises
        if self._tail is None:
            raise StopAsyncIteration
        return cast(StreamResponse, self._tail())

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, stream: _Stream) -> None:
        self.stream = stream
        self.cancelled: list[str] = []

    def send_message(self, message: Any, *, context: Any = None) -> _Stream:
        return self.stream

    async def cancel_task(self, request: Any, *, context: Any = None) -> None:
        self.cancelled.append(request.id)

    async def close(self) -> None:
        pass


async def _drive(stream: _Stream) -> tuple[list[Event], _Client, InMemoryTelemetry]:
    connector = InProcessConnector()
    telemetry = InMemoryTelemetry()
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=telemetry,
        state=InMemoryState(),
    )
    handle = "chassis.core.handle:echo_wire"
    await connector.setup({"connector": "inprocess", "handle": handle}, bundle)
    stub = _Client(stream)
    real: Any = connector._client
    connector._client = cast(Any, stub)
    try:
        events = [e async for e in connector.run(REQUEST, make_context(REQUEST))]
    finally:
        connector._client = real
        await connector.close()
    return events, stub, telemetry


def _start() -> StreamResponse:
    return _event({"type": "start", "request_id": REQUEST.request_id})


def _delta(text: str = "x") -> StreamResponse:
    return _event({"type": "delta", "text": text})


def _too_large(events: list[Event], stub: _Client, telemetry: InMemoryTelemetry) -> Error:
    error = events[-1]
    assert isinstance(error, Error) and error.code == connector_module.STREAM_TOO_LARGE
    assert not error.retryable and error.message == connector_module.STREAM_TOO_LARGE_TEXT
    assert [e for e in events if isinstance(e, Error)] == [error], "one terminal error"
    assert stub.stream.closed and stub.cancelled == ["task-1"], "the remote's task is cancelled"
    assert telemetry.counter_value("chassis.a2a.stream_capped", lane="inprocess") == 1
    [span] = [s for s in telemetry.spans if s.name == "chassis.engine.run"]
    assert span.attributes["a2a.stream_capped"] is True
    return error


async def test_a_remote_that_streams_past_the_event_cap_ends_with_one_stream_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2-11: past the event cap the run ends with one `a2a.stream_too_large` and a cancel."""
    cap = 20
    monkeypatch.setattr(connector_module, "STREAM_EVENTS_MAX", cap)
    stream = _Stream([_task(), _start()], tail=_delta)
    events, stub, telemetry = await _drive(stream)
    _too_large(events, stub, telemetry)
    assert len(events) <= cap + 1
    assert stream.read <= cap + 3, "the connector stops reading at the cap"


async def test_a_remote_that_streams_past_the_byte_cap_ends_with_one_stream_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2-11: past the byte cap (counted per stream item, before parsing) the run ends too."""
    monkeypatch.setattr(connector_module, "STREAM_BYTES_MAX", 2048)
    stream = _Stream([_task(), _start()], tail=lambda: _delta("y" * 500))
    events, stub, telemetry = await _drive(stream)
    _too_large(events, stub, telemetry)
    assert len(events) <= 6
    assert status_for(connector_module.STREAM_TOO_LARGE, False) == 502


async def test_a_first_item_past_the_byte_cap_still_cancels_its_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An item past the cap is never parsed, and its task is still cancelled."""
    monkeypatch.setattr(connector_module, "STREAM_BYTES_MAX", 64)
    stream = _Stream([_event({"type": "delta", "text": "z" * 200})])
    events, stub, telemetry = await _drive(stream)
    assert [e.type for e in events] == ["error"]
    _too_large(events, stub, telemetry)


async def test_a_stream_under_both_caps_is_unchanged() -> None:
    """The defaults let an ordinary run through."""
    assert connector_module.STREAM_EVENTS_MAX == 10_000
    assert connector_module.STREAM_BYTES_MAX == 4 * 1024 * 1024
    stream = _Stream([_task(), _start(), _delta(), _event({"type": "end"})])
    events, _, telemetry = await _drive(stream)
    assert [e.type for e in events] == ["start", "delta", "end"]
    assert telemetry.counter_value("chassis.a2a.stream_capped", lane="inprocess") == 0


async def test_a_remote_negative_token_count_is_a2a_bad_event() -> None:
    """R1-09: a `metrics` event with a negative token count fails the v0 bounds."""
    bad = _event({"type": "metrics", "input_tokens": -5, "output_tokens": 1})
    stream = _Stream([_task(), _start(), bad])
    events, _, _ = await _drive(stream)
    assert [e.type for e in events] == ["start", "error"]
    error = events[-1]
    assert isinstance(error, Error) and error.code == "a2a.bad_event"
    assert "-5" not in error.message


async def test_a_transport_failure_text_is_cut_to_the_error_message_cap() -> None:
    """A huge exception text is cut, so the `a2a.transport` event stays valid."""
    stream = _Stream([_task(), _start()], raises=httpx.ReadError("r" * (ERROR_MESSAGE_MAX + 50)))
    events, _, _ = await _drive(stream)
    error = events[-1]
    assert isinstance(error, Error) and error.code == "a2a.transport"
    assert len(error.message) == ERROR_MESSAGE_MAX
