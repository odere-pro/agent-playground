"""The template A2A server a workload ships with: an a2a-sdk `AgentExecutor` around one `handle`.

This is the workload-side copy of `chassis.adapters.a2a.server` (ADR-002, option A). It never
imports the chassis. The one difference in behavior: every yielded event is validated with
`jsonschema` against the vendored `schemas/events.v0.json` (the chassis copy uses `parse_event`),
and the defaults the schema names are filled in, so both copies put the same JSON on the wire.

`handle` is in its wire form: `(input: dict, ctx: dict)` in, event dicts out (or objects with
`model_dump`, which the server dumps). The server enforces the order the port promises: `start`
first, `end` or `error` last, nothing after. A workload failure never reaches the SDK's own
failure path: an exception is `error {code: "workload.exception"}`, a bad event (one that fails
the schema, or holds NaN or Infinity, which are not JSON) `error {code: "workload.bad_event"}`,
both with `TASK_STATE_FAILED`.

The request is read with `mapping.message_to_input`: `chassis.ctx` and `chassis.input` as JSON
strings, passed to `handle` as is. After the schema-version check and before `handle` runs, a value
there that is not a JSON object is `error {code: "a2a.bad_request"}` (suggested: the code name).

`PruningRequestHandler` deletes each task from the store once it is terminal, so a long-running
sidecar does not keep every task in memory (PoC-1 debt, 2026-09-29). `build_server` and `serve`
run the app on uvicorn, on loopback only unless `allow_any_host` is set (ADR-001: the sidecar
serves localhost only).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import ipaddress
import json
import logging
import signal
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from pathlib import Path
from types import FrameType
from typing import Any

import uvicorn
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
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from workload_a2a.mapping import (
    SCHEMA_VERSION_KEY,
    BadJson,
    dump_json,
    event_to_update,
    message_to_input,
)

logger = logging.getLogger(__name__)

WireHandle = Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[Any]]
"""`handle` as a workload writes it: dicts in, event dicts (or models with `model_dump`) out."""

BASE_URL = "http://127.0.0.1"
"""The URL the card names when the caller gives none: loopback, the only place a sidecar serves."""

SCHEMA_PATH = Path(__file__).parent / "schemas" / "events.v0.json"
"""The vendored copy of `packages/chassis/schemas/events.v0.json`; a test keeps them equal."""

_SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
_BRANCHES: dict[str, str] = {
    kind: ref.rsplit("/", 1)[-1] for kind, ref in _SCHEMA["discriminator"]["mapping"].items()
}
"""Event `type` to its `$defs` entry, from the schema's discriminator."""

SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = tuple(
    sorted({str(d["properties"]["schema_version"]["const"]) for d in _SCHEMA["$defs"].values()})
)
"""The versions this server accepts: the `schema_version` consts in the vendored schema."""
SCHEMA_VERSION = SUPPORTED_SCHEMA_VERSIONS[-1]

_VALIDATORS: dict[str, Draft202012Validator] = {
    kind: Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": _SCHEMA["$defs"]})
    for kind, name in _BRANCHES.items()
}
_FACTORY_DEFAULTS: dict[str, dict[str, Any]] = {"tool_call": {"arguments": {}}}
"""Defaults the chassis models build with a factory, which the JSON Schema does not carry."""
_DEFAULTS: dict[str, dict[str, Any]] = {
    kind: {
        **{
            prop: spec["default"]
            for prop, spec in _SCHEMA["$defs"][name]["properties"].items()
            if "default" in spec
        },
        **_FACTORY_DEFAULTS.get(kind, {}),
    }
    for kind, name in _BRANCHES.items()
}

_TERMINAL = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)


def validate_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one event against the vendored schema and fill its defaults. Raises `ValueError`
    with the reason. The result equals `parse_event(raw).model_dump(mode="json")` in the chassis.
    A missing `schema_version` is filled with the current one before anything is checked, as the
    chassis (a pydantic default) and the TypeScript server do (contract v1).
    """
    raw = {"schema_version": SCHEMA_VERSION, **raw}
    version = raw["schema_version"]
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"event schema version {version!r} is not supported; "
            f"this server accepts {', '.join(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    kind = raw.get("type")
    if not isinstance(kind, str) or kind not in _VALIDATORS:
        raise ValueError(f"event type {kind!r} is not one of {', '.join(_BRANCHES)}")
    validator = _VALIDATORS[kind]
    error = best_match(validator.iter_errors(raw))
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path)
        raise ValueError(f"{kind}: {error.message}" + (f" (at {where})" if where else ""))
    event = copy.deepcopy(_DEFAULTS[kind])
    event.update(raw)
    return event


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
                    dump_json(yielded)  # NaN and Infinity are not JSON
                    event = validate_event(yielded)
                except (ValueError, TypeError) as exc:
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


def is_loopback(host: str) -> bool:
    """`localhost` or a loopback IP (`127.0.0.0/8`, `::1`)."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class DrainingServer(uvicorn.Server):
    """A uvicorn server whose SIGTERM drains open A2A streams instead of cutting them.

    The A2A SDK streams with sse-starlette, which ends every open stream as soon as it sees a
    shutdown: through its patch of `uvicorn.Server.handle_exit`, or by finding the server behind
    the SIGTERM handler (`handler.__self__.should_exit`). Here the handler is a plain closure that
    sets only uvicorn's own flags, so neither path fires and `timeout_graceful_shutdown` decides
    when in-flight calls are cut. The first SIGTERM or SIGINT drains; a second one forces the exit.
    After a drain the process exits 0; the signal is not raised again.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        if threading.current_thread() is not threading.main_thread():
            yield
            return

        def on_signal(sig: int, frame: FrameType | None) -> None:
            if self.should_exit:
                self.force_exit = True
            self.should_exit = True

        previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


DEFAULT_DRAIN_TIMEOUT_S = 30
"""suggested: 30 s, the default `budget.timeout_ms`. How long a stopping workload waits for its
in-flight `handle` calls before it cancels them. Whole seconds, the type uvicorn takes."""


def build_server(
    handle: WireHandle,
    card: AgentCard,
    *,
    host: str = "127.0.0.1",
    port: int,
    uds: str | None = None,
    allow_any_host: bool = False,
    task_store: TaskStore | None = None,
    log_level: str = "warning",
    drain_timeout_s: int = DEFAULT_DRAIN_TIMEOUT_S,
) -> uvicorn.Server:
    """A uvicorn server for the app. `uds` serves on a Unix socket instead of TCP. A host that is
    not loopback is refused unless `allow_any_host` is set (ADR-001: localhost only).

    On SIGTERM uvicorn stops accepting, closes idle keep-alive connections, and waits up to
    `drain_timeout_s` for in-flight `handle` calls, streams included; then it cancels the rest.
    One server per process, so the signal is handled here, by `DrainingServer` (PoC-4 section 6).
    """
    if drain_timeout_s < 0:
        raise ValueError(f"drain_timeout_s must be 0 or more, got {drain_timeout_s}")
    if not allow_any_host and not is_loopback(host):
        raise ValueError(
            f"host {host!r} is not loopback; the sidecar serves localhost only (ADR-001). "
            "Pass allow_any_host to bind it anyway"
        )
    config = uvicorn.Config(
        build_app(handle, card, task_store=task_store),
        host=host,
        port=port,
        uds=uds,
        log_level=log_level,
        lifespan="off",
        timeout_graceful_shutdown=drain_timeout_s,
    )
    return DrainingServer(config)


def serve(
    handle: WireHandle,
    card: AgentCard,
    *,
    host: str = "127.0.0.1",
    port: int,
    uds: str | None = None,
    allow_any_host: bool = False,
) -> None:
    """Serve `handle` over A2A until the process is stopped."""
    build_server(handle, card, host=host, port=port, uds=uds, allow_any_host=allow_any_host).run()
