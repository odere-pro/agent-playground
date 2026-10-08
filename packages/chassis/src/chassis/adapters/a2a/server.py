"""The template A2A server: an a2a-sdk `AgentExecutor` that wraps one `handle`.

`handle` is in its wire form: `(input: dict, ctx: dict)` in, event dicts out (or objects with
`model_dump`, which the server dumps). Every yielded event is validated with `parse_event` before
it goes on the wire. The server enforces the order the port promises: `start` first, `end` or
`error` last, nothing after. A workload failure never reaches the SDK's own failure path: an
exception is `error {code: "workload.exception"}`, a bad event (one that fails `parse_event`, or
holds NaN or Infinity, which are not JSON) `error {code: "workload.bad_event"}`, both with
`TASK_STATE_FAILED`. Its one chassis import is `chassis.core.events`.

The request is read with `mapping.message_to_input`: `chassis.ctx` and `chassis.input` as JSON
strings, passed to `handle` as is. After the schema-version check and before `handle` runs, a value
there that is not a JSON object is `error {code: "a2a.bad_request"}` (suggested: the code name).

`PruningRequestHandler` deletes each task from the store once it is terminal, so a long-running
server does not keep every task in memory (PoC-1 debt, 2026-09-29).

`packages/workload-a2a` ships this file as `workload_a2a/server.py`, with `parse_event` replaced by
`jsonschema` validation against the vendored `events.v0.json`; keep the two in step.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from a2a.helpers import new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import Event, EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    CancelTaskRequest,
    Message,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import DEFAULT_RPC_URL, PROTOCOL_VERSION_CURRENT, TransportProtocol
from fastapi import FastAPI
from pydantic import ValidationError

from chassis.adapters.a2a.mapping import (
    SCHEMA_VERSION_KEY,
    BadJson,
    dump_json,
    event_to_update,
    message_to_input,
)
from chassis.core.events import SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS, parse_event

logger = logging.getLogger(__name__)

WireHandle = Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[Any]]
"""`handle` as a workload writes it: dicts in, event dicts (or models with `model_dump`) out."""

BASE_URL = "http://inprocess"
"""The URL the in-process app answers on. No socket: `httpx.ASGITransport` routes it."""

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


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "type": "error",
        "code": code,
        "message": message,
        "retryable": False,
    }


class HandleExecutor(AgentExecutor):
    """Runs `handle` for one task and publishes each event as one A2A update."""

    def __init__(self, handle: WireHandle) -> None:
        self._handle = handle
        self._running: dict[str, AsyncIterator[Any]] = {}
        self._cancelled: set[str] = set()
        """Tasks `cancel` has published `TASK_STATE_CANCELED` for. `execute` stops at its next
        step and closes the generator itself: `cancel` never calls `aclose()`, because a running
        generator raises `RuntimeError`, and one suspended at a `yield` may hold an anyio task
        group or cancel scope (PydanticAI, LangGraph) that must exit in the task that entered it."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        history = [context.message] if context.message is not None else []
        # a2a-sdk 1.2 needs the Task on the queue before the first status update.
        await event_queue.enqueue_event(
            new_task(task_id, context_id, TaskState.TASK_STATE_SUBMITTED, history=history)
        )
        updater = TaskUpdater(event_queue, task_id, context_id)

        version = str(context.metadata.get(SCHEMA_VERSION_KEY, SCHEMA_VERSION))
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            await event_to_update(
                _error(
                    "a2a.unsupported_schema_version",
                    f"schema version {version!r} is not supported; "
                    f"this server accepts {', '.join(SUPPORTED_SCHEMA_VERSIONS)}",
                ),
                updater,
            )
            return

        try:
            input, ctx = message_to_input(context)
        except BadJson as exc:
            await event_to_update(_error("a2a.bad_request", str(exc)), updater)
            return
        stream = self._handle(input, ctx)
        self._running[task_id] = stream
        started = False
        terminal = False
        seen_delta = False
        try:
            async for raw in stream:
                if task_id in self._cancelled:
                    return  # `cancel` already published TASK_STATE_CANCELED
                try:
                    yielded = _as_dict(raw)
                    dump_json(yielded)  # NaN and Infinity are not JSON; the dump would hide them
                    event = parse_event(yielded).model_dump(mode="json")
                except (ValidationError, ValueError, TypeError) as exc:
                    await event_to_update(_error("workload.bad_event", str(exc)), updater)
                    terminal = True
                    return
                kind = event["type"]
                if (kind == "start") == started:
                    await event_to_update(
                        _error("workload.bad_order", f"{kind!r} out of order"), updater
                    )
                    terminal = True
                    return
                started = True
                await event_to_update(event, updater, append=seen_delta)
                seen_delta = seen_delta or kind == "delta"
                if kind in ("end", "error"):
                    terminal = True
                    return
                if task_id in self._cancelled:
                    return  # cancelled while publishing: do not resume the generator
            if not terminal and task_id not in self._cancelled:
                await event_to_update(
                    _error("workload.no_end", "handle ended without `end` or `error`"), updater
                )
                terminal = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the workload's failure is one error event, not a 500
            if not terminal and task_id not in self._cancelled:
                await event_to_update(
                    _error("workload.exception", f"{type(exc).__name__}: {exc}"), updater
                )
        finally:
            self._running.pop(task_id, None)
            self._cancelled.discard(task_id)
            closer = getattr(stream, "aclose", None)
            if closer is not None:
                await closer()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        if self._running.pop(task_id, None) is not None:
            # Mid-await or suspended at a `yield`, the same: `execute` stops at its next step and
            # closes the generator in its own task. Nothing it publishes follows the CANCELED.
            self._cancelled.add(task_id)
        updater = TaskUpdater(event_queue, task_id, context.context_id or "")
        await updater.update_status(TaskState.TASK_STATE_CANCELED)


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


def build_agent_card(
    *, name: str, version: str, description: str = "", url: str = BASE_URL
) -> AgentCard:
    """The card the server publishes on `/.well-known/agent-card.json`: one skill, `handle`."""
    return AgentCard(
        name=name,
        description=description or f"chassis workload {name}",
        version=version,
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(
                id="handle",
                name="handle",
                description="handle(input, ctx) -> chassis events",
                tags=["chassis"],
            )
        ],
        supported_interfaces=[
            AgentInterface(
                url=url.rstrip("/") + DEFAULT_RPC_URL,
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
    )


def build_app(
    handle: WireHandle, card: AgentCard, *, task_store: TaskStore | None = None
) -> FastAPI:
    """The ASGI app: the agent card and the JSON-RPC endpoint, over `PruningRequestHandler`."""
    handler = PruningRequestHandler(
        agent_executor=HandleExecutor(handle),
        task_store=task_store if task_store is not None else InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI(title=f"a2a:{card.name}", version=card.version)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )
    return app
