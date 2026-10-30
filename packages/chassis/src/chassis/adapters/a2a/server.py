"""The template A2A server: an a2a-sdk `AgentExecutor` that wraps one `handle`.

`handle` is in its wire form: `(input: dict, ctx: dict)` in, event dicts out (or objects with
`model_dump`, which the server dumps). Every yielded event is validated with `parse_event` before
it goes on the wire. `task_store.drive_stream` enforces the order the port promises: `start`
first, `end` or `error` last, nothing after. A workload failure never reaches the SDK's own
failure path: an exception is `error {code: "workload.exception"}`, a bad event (one that fails
`parse_event`, or holds NaN or Infinity, which are not JSON) `error {code: "workload.bad_event"}`,
both with `TASK_STATE_FAILED`. Its one chassis import is `chassis.core.events`.

The request is read with `mapping.message_to_input`: `chassis.ctx` and `chassis.input` as JSON
strings, passed to `handle` as is. After the schema-version check and before `handle` runs, a value
there that is not a JSON object is `error {code: "a2a.bad_request"}` (suggested: the code name).

`task_store.PruningRequestHandler` deletes each task from the store once it is terminal.

`packages/workload-a2a` ships this file as `workload_a2a/server.py`, with `parse_event` replaced by
`jsonschema` validation against the vendored `events.v0.json`; keep the two in step. `task_store.py`
is a byte-for-byte copy there.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from a2a.helpers import new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
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
    TaskState,
)
from a2a.utils.constants import DEFAULT_RPC_URL, PROTOCOL_VERSION_CURRENT, TransportProtocol
from fastapi import FastAPI

from chassis.adapters.a2a.mapping import (
    SCHEMA_VERSION_KEY,
    BadJson,
    event_to_update,
    message_to_input,
)
from chassis.adapters.a2a.task_store import (
    PruningRequestHandler,
    close_stream,
    drive_stream,
    error_event,
)
from chassis.core.events import SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS, parse_event

WireHandle = Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[Any]]
"""`handle` as a workload writes it: dicts in, event dicts (or models with `model_dump`) out."""

BASE_URL = "http://inprocess"
"""The URL the in-process app answers on. No socket: `httpx.ASGITransport` routes it."""


def _validate(yielded: dict[str, Any]) -> dict[str, Any]:
    return parse_event(yielded).model_dump(mode="json")


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
                error_event(
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
            await event_to_update(error_event("a2a.bad_request", str(exc)), updater)
            return
        await self._run(self._handle(input, ctx), task_id, updater)

    async def _run(self, stream: AsyncIterator[Any], task_id: str, updater: TaskUpdater) -> None:
        self._running[task_id] = stream
        try:
            await drive_stream(stream, _validate, updater, lambda: task_id in self._cancelled)
        finally:
            self._running.pop(task_id, None)
            self._cancelled.discard(task_id)
            await close_stream(stream)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        if self._running.pop(task_id, None) is not None:
            # Mid-await or suspended at a `yield`, the same: `execute` stops at its next step and
            # closes the generator in its own task. Nothing it publishes follows the CANCELED.
            self._cancelled.add(task_id)
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
