"""What both template A2A servers share: the event order rules and the pruned task store.

This module imports a2a-sdk and its sibling `.mapping`, nothing else from a package of ours, so the
workload-side server can copy it unchanged (ADR-002). `packages/workload-a2a` holds a byte-for-byte
copy, and a test fails when the two differ. Change this copy first, then copy the file there.

`drive_stream` enforces the order the port promises: `start` first, `end` or `error` last, nothing
after. A workload failure never reaches the SDK's own failure path: it is one `workload.*` error
with `TASK_STATE_FAILED`. The validator is the one thing the two servers do differently, so each
passes its own. The caller registers the stream for `cancel`, and closes it when this returns.

`PruningRequestHandler` deletes each task from the store once it is terminal, so a long-running
server does not keep every task in memory (PoC-1 debt, 2026-09-29).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    CancelTaskRequest,
    Message,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatusUpdateEvent,
)

from .mapping import SCHEMA_VERSION, dump_json, event_to_update

logger = logging.getLogger(__name__)

Validate = Callable[[dict[str, Any]], dict[str, Any]]
"""One yielded event in, the wire event out. Raises `ValueError` or `TypeError` on a bad event."""

_TERMINAL = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)


def _as_dict(raw: Any) -> dict[str, Any]:
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    if isinstance(raw, dict):
        return dict(raw)
    raise TypeError(f"handle yielded {type(raw).__name__}, not an event dict")


def error_event(code: str, message: str) -> dict[str, Any]:
    """An `error` event of the server's own, not retryable."""
    return {
        "schema_version": SCHEMA_VERSION,
        "type": "error",
        "code": code,
        "message": message,
        "retryable": False,
    }


async def drive_stream(
    stream: AsyncIterator[Any],
    validate: Validate,
    updater: TaskUpdater,
    is_cancelled: Callable[[], bool],
) -> None:
    """Publish what `handle` yields, one A2A update per event, in the order the port promises.

    `is_cancelled` says `cancel` has published `TASK_STATE_CANCELED`; nothing is published after it.
    The caller closes `stream`, after it has dropped its own record of the run.
    """
    started = False
    terminal = False
    seen_delta = False
    try:
        async for raw in stream:
            if is_cancelled():
                return  # `cancel` already published TASK_STATE_CANCELED
            try:
                yielded = _as_dict(raw)
                dump_json(yielded)  # NaN and Infinity are not JSON; the dump would hide them
                event = validate(yielded)
            except (ValueError, TypeError) as exc:  # a pydantic ValidationError is a ValueError
                await event_to_update(error_event("workload.bad_event", str(exc)), updater)
                terminal = True
                return
            kind = event["type"]
            if (kind == "start") == started:
                await event_to_update(
                    error_event("workload.bad_order", f"{kind!r} out of order"), updater
                )
                terminal = True
                return
            started = True
            await event_to_update(event, updater, append=seen_delta)
            seen_delta = seen_delta or kind == "delta"
            if kind in ("end", "error"):
                terminal = True
                return
            if is_cancelled():
                return  # cancelled while publishing: do not resume the generator
        if not terminal and not is_cancelled():
            await event_to_update(
                error_event("workload.no_end", "handle ended without `end` or `error`"), updater
            )
            terminal = True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # the workload's failure is one error event, not a 500
        if not terminal and not is_cancelled():
            await event_to_update(
                error_event("workload.exception", f"{type(exc).__name__}: {exc}"), updater
            )


async def close_stream(stream: AsyncIterator[Any]) -> None:
    """Close the generator in the task that ran it, when it can be closed."""
    closer = getattr(stream, "aclose", None)
    if closer is not None:
        await closer()


def _terminal_state(event: Event | Task | Message) -> tuple[str, int] | None:
    """`(task_id, state)` when the event puts its task in a terminal state."""
    if isinstance(event, Task) and event.status.state in _TERMINAL:
        return event.id, event.status.state
    if isinstance(event, TaskStatusUpdateEvent) and event.status.state in _TERMINAL:
        return event.task_id, event.status.state
    return None


class PruningRequestHandler(DefaultRequestHandler):
    """`DefaultRequestHandler` that deletes a task from the store once it is terminal.

    a2a-sdk 1.2 saves every event to the store before it reaches a subscriber, so when the stream
    has yielded the terminal update, the terminal save is done and a delete cannot race it. A
    cancel is pruned by `on_cancel_task` after its own terminal write, not by the stream. A task
    whose client disconnects without a cancel and that then finishes in the background is not
    pruned; the chassis connector always cancels a task it leaves.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pruning: set[asyncio.Task[None]] = set()

    async def _prune(self, task_id: str, context: ServerCallContext) -> None:
        try:
            await self.task_store.delete(task_id, context)
        except Exception:  # pruning is housekeeping; never fail a request over it
            logger.exception("could not prune task %s", task_id)

    def _prune_soon(self, task_id: str, context: ServerCallContext) -> None:
        """A task of its own: the stream may be closing under a cancel, where an await would not
        run to the end."""
        task = asyncio.create_task(self._prune(task_id, context))
        self._pruning.add(task)
        task.add_done_callback(self._pruning.discard)

    async def on_message_send_stream(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event, None]:
        done: str | None = None
        try:
            async with contextlib.aclosing(super().on_message_send_stream(params, context)) as up:
                async for event in up:
                    terminal = _terminal_state(event)
                    if terminal is not None and terminal[1] != TaskState.TASK_STATE_CANCELED:
                        done = terminal[0]
                    yield event
        finally:
            if done:
                self._prune_soon(done, context)

    async def on_message_send(
        self, params: SendMessageRequest, context: ServerCallContext
    ) -> Task | Message:
        result: Task | Message = await super().on_message_send(params, context)
        terminal = _terminal_state(result)
        if terminal is not None:
            await self._prune(terminal[0], context)
        return result

    async def on_cancel_task(
        self, params: CancelTaskRequest, context: ServerCallContext
    ) -> Task | None:
        result: Task | None = await super().on_cancel_task(params, context)
        await self._prune(params.id, context)
        return result
