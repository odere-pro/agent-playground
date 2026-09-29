"""The template A2A server: an a2a-sdk `AgentExecutor` that wraps one `handle`.

`handle` is in its wire form: `(input: dict, ctx: dict)` in, event dicts out (or objects with
`model_dump`, which the server dumps). Every yielded event is validated with `parse_event` before
it goes on the wire. The server enforces the order the port promises: `start` first, `end` or
`error` last, nothing after. A workload failure never reaches the SDK's own failure path: an
exception is `error {code: "workload.exception"}`, a bad event `error {code: "workload.bad_event"}`,
both with `TASK_STATE_FAILED`. Its one chassis import is `chassis.core.events`.

In PoC-2 this file and `mapping.py` ship into the service template as `a2a_server.py`, with
`parse_event` replaced by `jsonschema` validation against the vendored `events.v0.json`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

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
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from a2a.utils.constants import DEFAULT_RPC_URL, PROTOCOL_VERSION_CURRENT, TransportProtocol
from fastapi import FastAPI
from pydantic import ValidationError

from chassis.adapters.a2a.mapping import (
    SCHEMA_VERSION_KEY,
    event_to_update,
    message_to_input,
)
from chassis.core.events import SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS, parse_event

WireHandle = Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[Any]]
"""`handle` as a workload writes it: dicts in, event dicts (or models with `model_dump`) out."""

BASE_URL = "http://inprocess"
"""The URL the in-process app answers on. No socket: `httpx.ASGITransport` routes it."""


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
        """Tasks cancelled while their generator was mid-await; `execute` stops at the next
        event. `aclose()` on a running generator raises `RuntimeError`."""

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

        input, ctx = message_to_input(context)
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
                    event = parse_event(_as_dict(raw)).model_dump(mode="json")
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
        stream = self._running.pop(task_id, None)
        if stream is not None:
            if getattr(stream, "ag_running", False):
                self._cancelled.add(task_id)  # mid-await: `execute` stops at its next event
            else:
                closer = getattr(stream, "aclose", None)
                if closer is not None:
                    await closer()
        updater = TaskUpdater(event_queue, task_id, context.context_id or "")
        await updater.update_status(TaskState.TASK_STATE_CANCELED)


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


def build_app(handle: WireHandle, card: AgentCard) -> FastAPI:
    """The ASGI app: the agent card and the JSON-RPC endpoint, over `DefaultRequestHandler`."""
    handler = DefaultRequestHandler(
        agent_executor=HandleExecutor(handle), task_store=InMemoryTaskStore(), agent_card=card
    )
    app = FastAPI(title=f"a2a:{card.name}", version=card.version)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )
    return app
