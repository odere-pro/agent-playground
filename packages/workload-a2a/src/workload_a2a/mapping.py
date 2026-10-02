"""Chassis events over A2A: the mapping, as pure functions over dicts.

This module imports a2a-sdk and nothing from `chassis`, so the service template can copy it
unchanged (ADR-002). The table it implements is in docs/contracts/contract-v0.md, "Chassis events
over A2A", with "Changes decided for v1", item 1. In short: one `Request` is one A2A task; every
chassis event is exactly one A2A stream event, in order, with the event's full JSON under
`metadata["chassis.event"]`; `context_id` is the trace id. Validation is not done here: the server
validates what the workload yields, and the connector validates what it reads back.

Chassis JSON crosses as a string (`dump_json`), not as a protobuf `Struct`, whose numbers are
doubles: `chassis.event`, `chassis.ctx`, and `chassis.input` (the whole `{text, data}`). Readers
(`load_json`) still take the v0 `Struct` form, as is, through v1; a string that does not parse to
a JSON object is `BadJson`. The native parts are unchanged: the view for generic A2A clients.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from a2a.helpers import new_data_part, new_text_part
from a2a.server.agent_execution import RequestContext
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, Role, SendMessageRequest, StreamResponse, TaskState
from google.protobuf import json_format

EVENT_KEY = "chassis.event"
"""On every A2A stream event: the chassis event as a JSON string, with its `schema_version`."""
CTX_KEY = "chassis.ctx"
"""On the `SendMessageRequest`: the `Context` (`context.v0.json`) as a JSON string."""
INPUT_KEY = "chassis.input"
"""On the `SendMessageRequest`: the whole `TaskInput`, `{text, data}`, as a JSON string."""
SCHEMA_VERSION_KEY = "chassis.schema_version"
"""On the `SendMessageRequest`: the event schema version the chassis speaks."""
OUTPUT_ARTIFACT_ID = "output"
"""The artifact every `delta` appends to."""
SCHEMA_VERSION = "0"
"""The schema version of the events this module synthesizes for A2A states of its own."""

_TERMINAL = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)
_UNSUPPORTED = frozenset({TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED})


class BadJson(ValueError):
    """A chassis metadata value that is not a JSON object: a string that does not parse to one,
    or a value of another type. The connector reports it as `a2a.bad_event`, the server as
    `a2a.bad_request`.
    """


def dump_json(value: Any) -> str:
    """The wire form of chassis JSON: compact, and `ValueError` on NaN or Infinity."""
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def load_json(metadata: dict[str, Any], key: str) -> dict[str, Any] | None:
    """`metadata[key]` as a dict, or None when it is missing. A string is parsed (v1); a dict is
    the v0 `Struct` form and is read as is, doubles and all (dropped in v2). Else `BadJson`.
    """
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        raise BadJson(f"{key}: must be a JSON string, got {type(value).__name__}")
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise BadJson(f"{key}: not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BadJson(f"{key}: must be a JSON object, got {type(parsed).__name__}")
    return parsed


# --- Request in ---


def request_to_message(
    input: dict[str, Any], ctx: dict[str, Any], *, schema_version: str = SCHEMA_VERSION
) -> SendMessageRequest:
    """One `Request` as one `SendMessageRequest`: `input` and `ctx` as JSON strings and the schema
    version in the request metadata, `trace_id` as `context_id`, and the native view for generic
    clients: `input.text` as a text part, `input.data` as a data part.
    """
    parts: list[Part] = []
    text = input.get("text")
    if text:
        parts.append(new_text_part(text))
    data = input.get("data")
    if data:
        parts.append(new_data_part(data, media_type="application/json"))
    message = Message(
        role=Role.ROLE_USER,
        message_id=uuid.uuid4().hex,
        context_id=str(ctx["trace_id"]),
        parts=parts,
    )
    return SendMessageRequest(
        message=message,
        metadata={
            CTX_KEY: dump_json(ctx),
            INPUT_KEY: dump_json(input),
            SCHEMA_VERSION_KEY: schema_version,
        },
    )


def message_to_input(context: RequestContext) -> tuple[dict[str, Any], dict[str, Any]]:
    """The other side of `request_to_message`: `(input, ctx)` as the dicts a workload sees.

    `ctx` is `chassis.ctx`, passed through unchanged. `input` is `chassis.input` as is, when it
    is set; else (a generic A2A client) the first text part and the first data part, whose numbers
    are doubles. Raises `BadJson` when either key holds something that is not a JSON object.
    """
    metadata = context.metadata
    ctx = load_json(metadata, CTX_KEY) or {}
    sent = load_json(metadata, INPUT_KEY)
    if sent is not None:
        return sent, ctx
    input: dict[str, Any] = {"text": None, "data": {}}
    text = context.get_user_input()
    if text:
        input["text"] = text
    if context.message is not None:
        for part in context.message.parts:
            if part.HasField("data"):
                input["data"] = json_format.MessageToDict(part.data)
                break
    return input, ctx


# --- Events out ---


async def event_to_update(
    event: dict[str, Any], updater: TaskUpdater, *, append: bool = False
) -> None:
    """Publish one chassis event as one A2A update through `updater`.

    `append` is False on the first `delta` and True after; the caller tracks it. The event's JSON
    travels as a string in `metadata[EVENT_KEY]`; the parts are the A2A-native view for generic
    clients. An event that is not JSON (NaN, Infinity) is a `ValueError`, before anything is
    published.
    """
    kind = event.get("type")
    metadata = {EVENT_KEY: dump_json(event)}
    if kind == "delta":
        await updater.add_artifact(
            [new_text_part(str(event.get("text", "")))],
            artifact_id=OUTPUT_ARTIFACT_ID,
            append=append,
            metadata=metadata,
        )
        return
    message: Message | None = None
    state = TaskState.TASK_STATE_WORKING
    if kind == "tool_call":
        message = updater.new_agent_message(
            [
                new_data_part(
                    {k: event.get(k) for k in ("call_id", "name", "arguments", "result")},
                    media_type="application/json",
                )
            ]
        )
    elif kind == "end":
        state = TaskState.TASK_STATE_COMPLETED
        if event.get("output") is not None:
            message = updater.new_agent_message(
                [new_data_part(event["output"], media_type="application/json")]
            )
    elif kind == "error":
        state = TaskState.TASK_STATE_FAILED
        message = updater.new_agent_message([new_text_part(str(event.get("message", "")))])
    await updater.update_status(state, message=message, metadata=metadata)


def _own(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "type": "error",
        "code": code,
        "message": message,
        "retryable": retryable,
    }


def _from_state(state: int, *, cancelled: bool) -> dict[str, Any] | None:
    """An A2A state with no chassis event behind it (the contract's "suggested" table)."""
    if state == TaskState.TASK_STATE_CANCELED:
        return None if cancelled else _own("a2a.canceled", "the task was canceled")
    if state == TaskState.TASK_STATE_COMPLETED:
        return {"schema_version": SCHEMA_VERSION, "type": "end", "status": "ok"}
    if state in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED):
        return _own("a2a.failed", f"the task ended in state {TaskState.Name(state)}")
    if state in _UNSUPPORTED:
        return _own("a2a.unsupported_state", f"{TaskState.Name(state)} is not in the contract")
    return None


def update_to_event(response: StreamResponse, *, cancelled: bool = False) -> dict[str, Any] | None:
    """The chassis event behind one A2A stream event, as a raw dict, or None when there is none.

    Reads only `metadata[EVENT_KEY]`, a JSON string or the v0 `Struct`; raises `BadJson` when it
    is neither. `cancelled` says the connector asked for the cancel, so a `TASK_STATE_CANCELED`
    ends the stream cleanly instead of becoming an error.
    """
    if response.HasField("status_update"):
        update = response.status_update
        event = load_json(json_format.MessageToDict(update.metadata), EVENT_KEY)
        if event is not None:
            return event
        return _from_state(update.status.state, cancelled=cancelled)
    if response.HasField("artifact_update"):
        return load_json(
            json_format.MessageToDict(response.artifact_update.artifact.metadata), EVENT_KEY
        )
    if response.HasField("task") and response.task.status.state in _TERMINAL:
        return _from_state(response.task.status.state, cancelled=cancelled)
    return None
