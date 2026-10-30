"""The stream rules of the workload template server: each bad event, bad order, no end, exception,
and cancel gives the `workload.*` code it gave before `drive_stream` (R2-05). The chassis server
has the same cases in `packages/chassis/tests/test_a2a_stream_order.py`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from workload_a2a import mapping
from workload_a2a.mapping import EVENT_KEY, request_to_message
from workload_a2a.server import SCHEMA_VERSION, HandleExecutor

START: dict[str, Any] = {"schema_version": "0", "type": "start", "request_id": "req-1"}
END: dict[str, Any] = {"schema_version": "0", "type": "end", "status": "ok"}


class _Queue(EventQueue):
    """Records what the updater enqueues. `EventQueue` is abstract; the SDK owns the real one."""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.published = asyncio.Event()

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)
        self.published.set()

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


def _context() -> RequestContext:
    return RequestContext(
        ServerCallContext(),
        request=request_to_message({"text": "hi"}, {"request_id": "req-1", "trace_id": "trace-1"}),
        task_id="task-1",
        context_id="trace-1",
    )


def _wire_events(queue: _Queue) -> list[dict[str, Any]]:
    updates = (TaskStatusUpdateEvent, TaskArtifactUpdateEvent)
    return [
        json.loads(e.metadata[EVENT_KEY])
        for e in queue.events
        if isinstance(e, updates) and EVENT_KEY in e.metadata
    ]


async def _future(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield START
    yield {"schema_version": "9", "type": "delta", "text": "from the future"}


async def _unknown_type(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield START
    yield {"schema_version": "0", "type": "thought", "text": "hmm"}


async def _extra_field(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {**START, "x": 1}


async def _not_a_dict(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield "start"


async def _infinity(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield START
    yield {"type": "metrics", "input_tokens": 1, "cost_usd": float("inf")}


async def _late_start(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "delta", "text": "no start"}


async def _second_start(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield START
    yield START


async def _raising(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield START
    raise RuntimeError("workload fell over")


async def _no_end(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield START


@pytest.mark.parametrize(
    ("handle", "code"),
    [
        (_future, "workload.bad_event"),
        (_unknown_type, "workload.bad_event"),
        (_extra_field, "workload.bad_event"),
        (_not_a_dict, "workload.bad_event"),
        (_infinity, "workload.bad_event"),
        (_late_start, "workload.bad_order"),
        (_second_start, "workload.bad_order"),
        (_raising, "workload.exception"),
        (_no_end, "workload.no_end"),
    ],
)
async def test_server_emits_one_workload_code_when_the_handle_misbehaves(
    handle: Any, code: str
) -> None:
    """A misbehaving handle ends in one `workload.*` error and a failed task."""
    queue = _Queue()

    await HandleExecutor(handle).execute(_context(), queue)

    events = _wire_events(queue)
    assert [e["code"] for e in events if e["type"] == "error"] == [code]
    assert events[-1]["retryable"] is False
    assert queue.events[-1].status.state == TaskState.TASK_STATE_FAILED


async def test_server_stops_and_closes_the_handle_when_it_yields_after_end() -> None:
    """Nothing after `end` is published, and the executor closes the handle."""
    closed: list[str] = []

    async def chatty(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        try:
            yield START
            yield END
            yield {"schema_version": "0", "type": "delta", "text": "too late"}
        finally:
            closed.append("closed")

    queue = _Queue()

    await HandleExecutor(chatty).execute(_context(), queue)

    assert [e["type"] for e in _wire_events(queue)] == ["start", "end"]
    assert closed == ["closed"]


async def _late_event() -> AsyncIterator[Any]:
    yield {"schema_version": "0", "type": "delta", "text": "too late"}


async def _late_failure() -> AsyncIterator[Any]:
    raise RuntimeError("fell over after the cancel")
    yield


async def _late_return() -> AsyncIterator[Any]:
    return
    yield


@pytest.mark.parametrize("after", [_late_event, _late_failure, _late_return])
async def test_server_publishes_nothing_more_when_the_handle_ends_after_a_cancel(
    after: Callable[[], AsyncIterator[Any]],
) -> None:
    """After a cancel, a later event, exception, or return publishes nothing."""
    gate = asyncio.Event()

    async def parked(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield START
        await gate.wait()
        async for event in after():
            yield event

    executor = HandleExecutor(parked)
    queue = _Queue()
    context = _context()
    run = asyncio.create_task(executor.execute(context, queue))
    await asyncio.wait_for(queue.published.wait(), timeout=1)
    await executor.cancel(context, queue)

    gate.set()
    await asyncio.wait_for(run, timeout=1)

    assert [e["type"] for e in _wire_events(queue)] == ["start"]
    assert queue.events[-1].status.state == TaskState.TASK_STATE_CANCELED


def test_mapping_version_equals_the_server_version_when_an_error_is_built() -> None:
    """`error_event` stamps the mapping's version; it must equal the vendored schema's."""
    assert mapping.SCHEMA_VERSION == SCHEMA_VERSION
