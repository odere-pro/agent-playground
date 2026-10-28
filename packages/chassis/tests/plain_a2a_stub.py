"""A plain-A2A stub agent: an a2a-sdk server that sends NO `chassis.event` metadata, the way a
third-party agent (kagent-adk) does. Not a test module; the connector tests and the PoC-6b contract
binding import it. It serves over a Unix socket (`a2a_uds.serve_uds`), which the gate allows.

`kagent_stream` is the shape the 2026-10-09 probe recorded from kagent-adk 0.4.0 (a2a-sdk 1.1.5):
`pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-kagent-probe.md`, item 3. The builders make
`StreamResponse` values, so the pure translator tests feed the same items the server sends.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import DEFAULT_RPC_URL, PROTOCOL_VERSION_CURRENT, TransportProtocol
from fastapi import FastAPI
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Struct

USAGE_KEY = "kagent.dev/a2a/usage"
"""The metadata key the probe saw kagent-adk put token usage under."""
ANSWER = "Plain words. Short sentences. Same facts."
CHUNKS = ("Plain ", "words. ", "Short ", "sentences. ", "Same ", "facts.")
"""The probe's six chunks: the first has no `append`, the five after it have `append=true`."""
KAGENT_USAGE = {"promptTokenCount": 42, "candidatesTokenCount": 9, "totalTokenCount": 51}

TASK_ID = "task-1"
CONTEXT_ID = "ctx-1"

S = TaskState


def struct(value: dict[str, Any] | None) -> Struct:
    out = Struct()
    if value:
        json_format.ParseDict(value, out)
    return out


def _message(text: str | None) -> Message:
    """An agent message. `text=None` and no `parts`: no parts at all (kagent's empty one)."""
    message = Message(role=Role.ROLE_AGENT, message_id="m", task_id=TASK_ID)
    if text is not None:
        message.parts.append(Part(text=text))
    return message


def task(
    state: int,
    *,
    text: str | None = None,
    artifacts: Sequence[tuple[str, str]] = (),
    usage: dict[str, Any] | None = None,
    history_text: str | None = None,
    metadata: dict[str, Any] | None = None,
    message_parts: Sequence[Part] = (),
) -> StreamResponse:
    status = TaskStatus(state=cast(TaskState, state))
    if text is not None or message_parts:
        status.message.CopyFrom(_message(text))
        status.message.parts.extend(message_parts)
    out = Task(id=TASK_ID, context_id=CONTEXT_ID, status=status)
    for artifact_id, body in artifacts:
        out.artifacts.append(Artifact(artifact_id=artifact_id, parts=[Part(text=body)]))
    if history_text is not None:
        out.history.append(
            Message(role=Role.ROLE_USER, message_id="h", parts=[Part(text=history_text)])
        )
    meta = dict(metadata or {})
    if usage is not None:
        meta[USAGE_KEY] = usage
    out.metadata.CopyFrom(struct(meta))
    return StreamResponse(task=out)


def status(
    state: int,
    *,
    text: str | None = None,
    usage: Any = None,
    metadata: dict[str, Any] | None = None,
    message_parts: Sequence[Part] = (),
    task_id: str = TASK_ID,
) -> StreamResponse:
    """A status update; `usage` goes under `USAGE_KEY` in the update metadata."""
    update = TaskStatusUpdateEvent(
        task_id=task_id, context_id=CONTEXT_ID, status=TaskStatus(state=cast(TaskState, state))
    )
    if text is not None or message_parts:
        update.status.message.CopyFrom(_message(text))
        update.status.message.parts.extend(message_parts)
    meta = dict(metadata or {})
    if usage is not None:
        meta[USAGE_KEY] = usage
    update.metadata.CopyFrom(struct(meta))
    return StreamResponse(status_update=update)


def empty_working() -> StreamResponse:
    """What kagent sends before COMPLETED: WORKING with an agent message that has no parts."""
    response = status(S.TASK_STATE_WORKING)
    response.status_update.status.message.CopyFrom(_message(None))
    return response


def artifact(
    text: str,
    *,
    append: bool = False,
    last_chunk: bool = False,
    usage: Any = None,
    artifact_id: str = "A",
    event_metadata: dict[str, Any] | None = None,
    parts: Sequence[Part] = (),
) -> StreamResponse:
    update = TaskArtifactUpdateEvent(
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        append=append,
        last_chunk=last_chunk,
        artifact=Artifact(artifact_id=artifact_id, parts=[Part(text=text), *parts]),
    )
    if usage is not None:
        update.artifact.metadata.CopyFrom(struct({USAGE_KEY: usage}))
    if event_metadata:
        update.metadata.CopyFrom(struct(event_metadata))
    return StreamResponse(artifact_update=update)


def reply(text: str) -> StreamResponse:
    """A `message` reply with no task."""
    return StreamResponse(
        message=Message(role=Role.ROLE_AGENT, message_id="m", parts=[Part(text=text)])
    )


def kagent_stream() -> list[StreamResponse]:
    """The probe's stream, item by item (note, item 3)."""
    return [
        task(S.TASK_STATE_SUBMITTED, history_text="the user's own message"),
        status(S.TASK_STATE_WORKING),
        artifact(CHUNKS[0]),
        *[artifact(c, append=True) for c in CHUNKS[1:]],
        artifact(ANSWER, last_chunk=True, usage=KAGENT_USAGE),
        empty_working(),
        status(S.TASK_STATE_COMPLETED, usage=KAGENT_USAGE),
    ]


# --- the server ---

Script = Callable[[RequestContext], Sequence[StreamResponse]]
"""Maps the request to the stream the stub sends. The first item must be a `task`: a2a-sdk's own
server wants the Task on the queue first (a fake client covers other first items)."""


def _payload(response: StreamResponse) -> Any:
    return getattr(response, str(response.WhichOneof("payload")))


@dataclass
class PlainStub:
    """A scripted `AgentExecutor`. Records what it was asked, and the cancels."""

    script: Script
    hang_after: bool = False
    """Keep the stream open after the script, as an agent that is paused or still working."""
    messages: list[Message] = field(default_factory=list)
    """The user message of each request, as the stub received it."""
    metadata: list[dict[str, Any]] = field(default_factory=list)
    """The metadata of each `SendMessageRequest`, as the stub received it."""
    cancels: list[str] = field(default_factory=list)

    def executor(self) -> AgentExecutor:
        stub = self

        class _Executor(AgentExecutor):
            async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
                if context.message is not None:
                    stub.messages.append(Message.FromString(context.message.SerializeToString()))
                stub.metadata.append(dict(context.metadata))
                for item in stub.script(context):
                    event = _payload(item)
                    if isinstance(event, Task):
                        event.id = context.task_id or ""
                    if isinstance(event, Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent):
                        event.context_id = context.context_id or ""
                    if isinstance(event, TaskStatusUpdateEvent | TaskArtifactUpdateEvent):
                        event.task_id = context.task_id or ""
                    await event_queue.enqueue_event(event)
                if stub.hang_after:
                    await asyncio.Event().wait()

            async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
                stub.cancels.append(context.task_id or "")
                updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
                await updater.update_status(TaskState.TASK_STATE_CANCELED)

        return _Executor()


@dataclass
class Bodies:
    """ASGI middleware: the parsed JSON-RPC body of each POST, as the client put it on the wire
    (the SDK server fills in a `contextId` after that, so the executor cannot tell)."""

    app: Any
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()

        async def recording(*args: Any) -> Any:
            message = await receive(*args)
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if not message.get("more_body") and body:
                    self.sent.append(json.loads(bytes(body)))
            return message

        await self.app(scope, recording, send)


def probe_card(url: str = "http://127.0.0.1:18080") -> AgentCard:
    """The card the probe fetched from kagent-adk (`notes/kagent-probe/agent-card.json`)."""
    return AgentCard(
        name="probe_agent",
        description="probe agent",
        version="v1",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text"],
        default_output_modes=["text"],
        supported_interfaces=[
            AgentInterface(
                url=url,
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
    )


def stub_app(stub: PlainStub, card: AgentCard | None = None) -> FastAPI:
    """The ASGI app: the card at the well-known path and JSON-RPC at the card's path (`/`)."""
    card = card or probe_card()
    handler = DefaultRequestHandler(
        agent_executor=stub.executor(), task_store=InMemoryTaskStore(), agent_card=card
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=DEFAULT_RPC_URL),
    )
    return app


def kagent_script(_: RequestContext) -> Sequence[StreamResponse]:
    return kagent_stream()


__all__ = [
    "ANSWER",
    "CHUNKS",
    "KAGENT_USAGE",
    "USAGE_KEY",
    "Bodies",
    "PlainStub",
    "S",
    "artifact",
    "empty_working",
    "kagent_script",
    "kagent_stream",
    "probe_card",
    "reply",
    "status",
    "struct",
    "stub_app",
    "task",
]
