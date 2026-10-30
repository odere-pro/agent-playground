"""Stream readers for `InboundAdapterContract` bindings: what a client of each format reads from a
stream, as a `Readback`, with the format's own shape checked frame by frame.

A reader fails with an `AssertionError` that names the frame (its index and event) or the block
it is about, so a wrong stream points at its first wrong frame. The OpenAI and Anthropic readers
use the official SDK types, imported when called: this module is not exported from
`chassis_contracts` (as `interface.py`), so importing the package needs neither SDK. Import it as
`chassis_contracts.stream_readers`.

Each reader is a fold: one frozen state, one step per frame, then a final check.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from chassis.core.envelope import Response
from chassis.core.events import Error, parse_event
from pydantic import TypeAdapter

from chassis_contracts.inbound import AGENT, Readback, ReadError, SSEEvent, Usage, sse

__all__ = [
    "anthropic_error",
    "anthropic_read_stream",
    "lower",
    "native_read_stream",
    "openai_error",
    "openai_read_stream",
    "should_retry",
    "type_adapter",
]

DONE = "[DONE]"
OPENAI_ID_PREFIX = "chatcmpl-"
ANTHROPIC_ID_PREFIX = "msg_"
TOOL_BLOCKS = ("tool_use", "server_tool_use")


# --- Shared ---


def lower(headers: Mapping[str, str]) -> dict[str, str]:
    """The headers with lower-case names."""
    return {k.lower(): v for k, v in headers.items()}


def should_retry(headers: Mapping[str, str]) -> bool | None:
    """The `x-should-retry` header as a bool; None when absent."""
    value = lower(headers).get("x-should-retry")
    if value is None:
        return None
    assert value in ("true", "false"), f"x-should-retry: {value!r}"
    return value == "true"


@functools.cache
def type_adapter(tp: Any) -> TypeAdapter[Any]:
    """One `TypeAdapter` per type, kept alive: a lazy `ValidatorIterator` it returns refers to its
    validator, and pydantic-core panics when it is read after the adapter is collected (FastAPI
    keeps its own alive the same way).
    """
    return TypeAdapter(tp)


def _request_id(message_id: str, prefix: str) -> str:
    assert message_id.startswith(prefix), f"id {message_id!r} does not start with {prefix!r}"
    return message_id.removeprefix(prefix)


# --- Native: one frame per event, then the `response` frame ---


def native_read_stream(status: int, headers: Mapping[str, str], frames: Sequence[str]) -> Readback:
    """One frame per event, then one `response` frame with the envelope, last."""
    events = sse(frames)
    assert events and events[-1].event == "response", "the stream ends with the response frame"
    text: list[str] = []
    error: ReadError | None = None
    for index, frame in enumerate(events[:-1]):
        event = parse_event(json.loads(frame.data))
        assert frame.event == event.type, (
            f"frame {index}: name {frame.event!r} != event type {event.type!r}"
        )
        if event.type == "delta":
            assert error is None, f"frame {index}: a delta after the error"
            text.append(event.text)
        elif isinstance(event, Error):
            error = ReadError(message=event.message, code=event.code)
    response = Response.model_validate_json(events[-1].data)
    metrics = response.metrics
    return Readback(
        text="".join(text),
        usage=Usage(metrics["input_tokens"], metrics["output_tokens"]),
        finish=None,
        status=status,
        error=error,
        run_status=response.status,
        request_id=response.request_id,
    )


# --- OpenAI: `data:` chunks, then `data: [DONE]` ---


def openai_error(headers: Mapping[str, str] | None, payload: dict[str, Any]) -> ReadError:
    """`{"error": {...}}` read as the SDK's `ErrorObject`; `headers` None inside a stream."""
    from openai.types.shared import ErrorObject

    error = ErrorObject.model_validate(payload["error"])
    return ReadError(
        message=error.message,
        code=error.code,
        type=error.type,
        should_retry=None if headers is None else should_retry(headers),
    )


@dataclass(frozen=True)
class _OpenAIStream:
    text: tuple[str, ...] = ()
    usage: Usage | None = None
    finish: str | None = None
    error: ReadError | None = None
    tool_calls: int = 0
    ids: frozenset[str] = frozenset()
    role_seen: bool = False


def _openai_choice(state: _OpenAIStream, choice: Any, where: str) -> _OpenAIStream:
    assert state.finish is None, f"{where}: a choice after the finish chunk"
    assert state.role_seen or choice.delta.role == "assistant", (
        f"{where}: the first chunk does not name the role assistant"
    )
    delta = choice.delta
    calls = len(delta.tool_calls or []) + (delta.function_call is not None)
    return replace(
        state,
        text=(*state.text, delta.content) if delta.content else state.text,
        tool_calls=state.tool_calls + calls,
        finish=choice.finish_reason if choice.finish_reason is not None else state.finish,
        role_seen=True,
    )


def _openai_usage(state: _OpenAIStream, chunk: Any, where: str) -> Usage | None:
    if chunk.usage is None:
        return state.usage
    assert state.usage is None, f"{where}: usage is sent once"
    u = chunk.usage
    assert u.total_tokens == u.prompt_tokens + u.completion_tokens, f"{where}: total_tokens"
    return Usage(u.prompt_tokens, u.completion_tokens)


def _openai_step(state: _OpenAIStream, index: int, raw: str) -> _OpenAIStream:
    from openai.types.chat import ChatCompletionChunk

    where = f"frame {index}"
    assert state.error is None, f"{where}: something other than [DONE] after the error frame"
    payload = json.loads(raw)
    if "error" in payload:
        return replace(state, error=openai_error(None, payload))
    chunk = ChatCompletionChunk.model_validate(payload)
    assert chunk.object == "chat.completion.chunk", f"{where}: object {chunk.object!r}"
    assert chunk.model == AGENT, f"{where}: model {chunk.model!r}"
    state = replace(state, ids=state.ids | {chunk.id}, usage=_openai_usage(state, chunk, where))
    return functools.reduce(lambda s, c: _openai_choice(s, c, where), chunk.choices, state)


def _openai_request_id(ids: frozenset[str]) -> str | None:
    assert len(ids) <= 1, f"one id per stream: {sorted(ids)}"
    return next((_request_id(i, OPENAI_ID_PREFIX) for i in ids), None)


def openai_read_stream(status: int, headers: Mapping[str, str], frames: Sequence[str]) -> Readback:
    """`data:` chunks, the first one `delta {role: assistant}`, at most one error frame, then
    `data: [DONE]` last."""
    data = [event.data for event in sse(frames)]
    assert data and data[-1] == DONE, "the stream ends with data: [DONE]"
    assert DONE not in data[:-1], f"one [DONE]: frame {data.index(DONE)} is another"
    state = _OpenAIStream()
    for index, raw in enumerate(data[:-1]):
        state = _openai_step(state, index, raw)
    return Readback(
        text="".join(state.text),
        usage=state.usage,
        finish=state.finish,
        status=status,
        error=state.error,
        run_status=lower(headers).get("x-chassis-status"),
        request_id=_openai_request_id(state.ids),
        tool_calls=state.tool_calls,
    )


# --- Anthropic: `message_start`, blocks, `message_delta`, `message_stop` ---


def anthropic_error(headers: Mapping[str, str] | None, payload: dict[str, Any]) -> ReadError:
    """`{"type": "error", "error": {"type", "message": "<code>: <message>"}}`."""
    from anthropic.types import ErrorResponse

    response = ErrorResponse.model_validate(payload)
    assert response.type == "error"
    message = response.error.message
    code, sep, _ = message.partition(": ")
    return ReadError(
        message=message,
        code=code if sep else None,
        type=response.error.type,
        should_retry=None if headers is None else should_retry(headers),
    )


@dataclass(frozen=True)
class _AnthropicStream:
    text: tuple[str, ...] = ()
    finish: str | None = None
    usage: Usage | None = None
    error: ReadError | None = None
    request_id: str | None = None
    input_tokens: int = 0
    tool_calls: int = 0
    open_blocks: frozenset[int] = frozenset()
    stopped: bool = False


AnthropicStep = Callable[[_AnthropicStream, Any, str], _AnthropicStream]


def _on_message_start(state: _AnthropicStream, event: Any, where: str) -> _AnthropicStream:
    message = event.message
    assert message.content == [] and message.stop_reason is None, (
        f"{where}: message_start carries content or a stop reason"
    )
    assert message.model == AGENT, f"{where}: model {message.model!r}"
    return replace(
        state,
        request_id=_request_id(message.id, ANTHROPIC_ID_PREFIX),
        input_tokens=message.usage.input_tokens,
    )


def _on_block_start(state: _AnthropicStream, event: Any, where: str) -> _AnthropicStream:
    block = event.content_block
    is_text = block.type == "text"
    return replace(
        state,
        open_blocks=state.open_blocks | {event.index},
        text=(*state.text, block.text) if is_text else state.text,
        tool_calls=state.tool_calls + (not is_text and block.type in TOOL_BLOCKS),
    )


def _on_block_delta(state: _AnthropicStream, event: Any, where: str) -> _AnthropicStream:
    assert event.index in state.open_blocks, (
        f"{where}: a delta for block {event.index}, which is not open"
    )
    if event.delta.type != "text_delta":
        return state
    return replace(state, text=(*state.text, event.delta.text))


def _on_block_stop(state: _AnthropicStream, event: Any, where: str) -> _AnthropicStream:
    assert event.index in state.open_blocks, (
        f"{where}: a stop for block {event.index}, which is not open"
    )
    return replace(state, open_blocks=state.open_blocks - {event.index})


def _on_message_delta(state: _AnthropicStream, event: Any, where: str) -> _AnthropicStream:
    assert not state.open_blocks, (
        f"{where}: message_delta with block {sorted(state.open_blocks)} still open"
    )
    given = event.usage.input_tokens
    usage = Usage(state.input_tokens if given is None else given, event.usage.output_tokens)
    return replace(state, finish=event.delta.stop_reason, usage=usage)


def _on_message_stop(state: _AnthropicStream, event: Any, where: str) -> _AnthropicStream:
    return replace(state, stopped=True)


def _unchanged(state: _AnthropicStream, event: Any, where: str) -> _AnthropicStream:
    return state


ANTHROPIC_STEPS: Mapping[str, AnthropicStep] = {
    "message_start": _on_message_start,
    "content_block_start": _on_block_start,
    "content_block_delta": _on_block_delta,
    "content_block_stop": _on_block_stop,
    "message_delta": _on_message_delta,
    "message_stop": _on_message_stop,
}


def _anthropic_step(state: _AnthropicStream, index: int, frame: SSEEvent) -> _AnthropicStream:
    from anthropic.types import RawMessageStreamEvent

    payload = json.loads(frame.data)
    where = f"frame {index} ({payload['type']})"
    assert frame.event == payload["type"], f"{where}: event name {frame.event!r}"
    assert state.error is None and not state.stopped, f"{where}: after the end of the stream"
    if payload["type"] == "error":
        return replace(state, error=anthropic_error(None, payload))
    if payload["type"] == "ping":
        return state
    event = type_adapter(RawMessageStreamEvent).validate_python(payload)
    assert (event.type == "message_start") == (index == 0), (
        f"{where}: message_start comes first, once"
    )
    return ANTHROPIC_STEPS.get(event.type, _unchanged)(state, event, where)


def anthropic_read_stream(
    status: int, headers: Mapping[str, str], frames: Sequence[str]
) -> Readback:
    """`message_start` first; text blocks opened before their deltas and closed; then
    `message_delta` and `message_stop` last, or `event: error` last with no `message_stop`."""
    events = sse(frames)
    assert events, "an empty stream"
    state = _AnthropicStream()
    for index, frame in enumerate(events):
        state = _anthropic_step(state, index, frame)
    assert state.error is not None or state.stopped, (
        "a stream without an error ends with message_stop"
    )
    return Readback(
        text="".join(state.text),
        usage=state.usage,
        finish=state.finish,
        status=status,
        error=state.error,
        run_status=lower(headers).get("x-chassis-status"),
        request_id=state.request_id,
        tool_calls=state.tool_calls,
    )
