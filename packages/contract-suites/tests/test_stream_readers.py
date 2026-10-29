"""Direct tests of `chassis_contracts.stream_readers`: a good stream reads back as the client sees
it, and a bad stream fails naming its first wrong frame or block. A reader bug would otherwise
hide an adapter bug in `packages/chassis/tests/test_inbound_contract.py`.

The frames are written by hand here, never by an adapter, so a reader is checked on its own.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from chassis.core.envelope import Response, Versions
from chassis.core.events import Delta, Error, Event, Start
from chassis_contracts.inbound import AGENT, Readback, Usage
from chassis_contracts.stream_readers import (
    anthropic_read_stream,
    native_read_stream,
    openai_read_stream,
)

REQUEST_ID = "req-1"
NO_HEADERS: dict[str, str] = {}


def _named(payload: dict[str, Any]) -> str:
    """One SSE frame named after the payload's `type`."""
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n"


def _data(payload: dict[str, Any] | str) -> str:
    """One `data:` frame, JSON unless it is already text."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {text}\n\n"


# --- Anthropic frames ---

MESSAGE_START: dict[str, Any] = {
    "type": "message_start",
    "message": {
        "id": f"msg_{REQUEST_ID}",
        "type": "message",
        "role": "assistant",
        "model": AGENT,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 0},
    },
}
MESSAGE_DELTA: dict[str, Any] = {
    "type": "message_delta",
    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
    "usage": {"output_tokens": 5},
}
MESSAGE_STOP: dict[str, Any] = {"type": "message_stop"}
ANTHROPIC_ERROR: dict[str, Any] = {
    "type": "error",
    "error": {"type": "overloaded_error", "message": "model.overloaded: try again"},
}


TEXT_BLOCK: dict[str, Any] = {"type": "text", "text": ""}
TOOL_USE_BLOCK: dict[str, Any] = {"type": "tool_use", "id": "t1", "name": "f", "input": {}}


def _block_start(index: int, block: dict[str, Any] = TEXT_BLOCK) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def _text_delta(index: int, text: str) -> dict[str, Any]:
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    }


def _block_stop(index: int) -> dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


def _anthropic(*payloads: dict[str, Any]) -> list[str]:
    return [_named(p) for p in payloads]


ANTHROPIC_ANSWER = _anthropic(
    MESSAGE_START,
    _block_start(0),
    _text_delta(0, "Hello, "),
    _text_delta(0, "world."),
    _block_stop(0),
    MESSAGE_DELTA,
    MESSAGE_STOP,
)


def test_anthropic_reader_reads_the_answer_when_the_stream_is_well_formed() -> None:
    """A well-formed stream reads as its text, usage, finish, and request id."""
    read = anthropic_read_stream(200, NO_HEADERS, ANTHROPIC_ANSWER)

    assert read == Readback(
        text="Hello, world.",
        usage=Usage(7, 5),
        finish="end_turn",
        status=200,
        error=None,
        request_id=REQUEST_ID,
    )


def test_anthropic_reader_reads_the_error_when_the_stream_ends_with_an_error_frame() -> None:
    """An error frame after text is the error, with the code from its message and no finish."""
    frames = _anthropic(MESSAGE_START, _block_start(0), _text_delta(0, "part"), ANTHROPIC_ERROR)

    read = anthropic_read_stream(200, NO_HEADERS, frames)

    assert (read.text, read.finish) == ("part", None)
    assert read.error is not None
    assert read.error.code == "model.overloaded"


def test_anthropic_reader_counts_a_tool_use_block_when_the_stream_has_one() -> None:
    """A `tool_use` block is a tool call the client is asked to run."""
    frames = _anthropic(
        MESSAGE_START, _block_start(0, TOOL_USE_BLOCK), _block_stop(0), MESSAGE_DELTA, MESSAGE_STOP
    )

    read = anthropic_read_stream(200, NO_HEADERS, frames)

    assert read.tool_calls == 1


@pytest.mark.parametrize(
    ("frames", "names"),
    [
        pytest.param(
            _anthropic(MESSAGE_START, _block_start(0), _text_delta(1, "x")),
            r"frame 2 \(content_block_delta\): a delta for block 1, which is not open",
            id="delta-for-a-block-not-open",
        ),
        pytest.param(
            _anthropic(MESSAGE_START, _block_stop(0)),
            r"frame 1 \(content_block_stop\): a stop for block 0, which is not open",
            id="stop-for-a-block-not-open",
        ),
        pytest.param(
            _anthropic(MESSAGE_START, _block_start(0), MESSAGE_DELTA),
            r"frame 2 \(message_delta\): message_delta with block \[0\] still open",
            id="message-delta-with-a-block-open",
        ),
        pytest.param(
            _anthropic(_block_start(0)),
            r"frame 0 \(content_block_start\): message_start comes first",
            id="message-start-not-first",
        ),
        pytest.param(
            _anthropic(MESSAGE_START, MESSAGE_START),
            r"frame 1 \(message_start\): message_start comes first, once",
            id="message-start-twice",
        ),
        pytest.param(
            _anthropic(MESSAGE_START, MESSAGE_DELTA, MESSAGE_STOP, MESSAGE_STOP),
            r"frame 3 \(message_stop\): after the end of the stream",
            id="frame-after-message-stop",
        ),
        pytest.param(
            _anthropic(MESSAGE_START, ANTHROPIC_ERROR, MESSAGE_STOP),
            r"frame 2 \(message_stop\): after the end of the stream",
            id="frame-after-the-error",
        ),
        pytest.param(
            _anthropic(MESSAGE_START, MESSAGE_DELTA),
            "a stream without an error ends with message_stop",
            id="no-message-stop",
        ),
        pytest.param(
            [f"event: ping\ndata: {json.dumps(MESSAGE_START)}\n\n"],
            r"frame 0 \(message_start\): event name 'ping'",
            id="event-name-is-not-the-type",
        ),
        pytest.param([], "an empty stream", id="empty"),
    ],
)
def test_anthropic_reader_fails_naming_the_frame_when_the_stream_is_malformed(
    frames: Sequence[str], names: str
) -> None:
    """A malformed stream fails, and the message names the frame or block that is wrong."""
    with pytest.raises(AssertionError, match=names):
        anthropic_read_stream(200, NO_HEADERS, frames)


# --- OpenAI frames ---

CHUNK_ID = f"chatcmpl-{REQUEST_ID}"


def _chunk(
    delta: dict[str, Any] | None = None,
    *,
    finish: str | None = None,
    chunk_id: str = CHUNK_ID,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": 0,
        "model": AGENT,
        "choices": choices,
        "usage": usage,
    }


USAGE = {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}
ROLE = _chunk({"role": "assistant", "content": ""})
OPENAI_ERROR: dict[str, Any] = {
    "error": {"message": "try again", "type": "server_error", "code": "model.overloaded"}
}


def _openai(*payloads: dict[str, Any] | str) -> list[str]:
    return [_data(p) for p in payloads]


OPENAI_ANSWER = _openai(
    ROLE,
    _chunk({"content": "Hello, "}),
    _chunk({"content": "world."}),
    _chunk({}, finish="stop"),
    _chunk(usage=USAGE),
    "[DONE]",
)


def test_openai_reader_reads_the_answer_when_the_stream_is_well_formed() -> None:
    """A well-formed stream reads as its text, usage, finish, and request id."""
    read = openai_read_stream(200, {"X-Chassis-Status": "ok"}, OPENAI_ANSWER)

    assert read == Readback(
        text="Hello, world.",
        usage=Usage(7, 5),
        finish="stop",
        status=200,
        error=None,
        run_status="ok",
        request_id=REQUEST_ID,
    )


def test_openai_reader_reads_the_error_when_the_stream_has_an_error_frame() -> None:
    """An error frame before `[DONE]` is the error, with its code and no finish."""
    frames = _openai(ROLE, _chunk({"content": "part"}), OPENAI_ERROR, "[DONE]")

    read = openai_read_stream(200, NO_HEADERS, frames)

    assert (read.text, read.finish) == ("part", None)
    assert read.error is not None
    assert read.error.code == "model.overloaded"


@pytest.mark.parametrize(
    ("frames", "names"),
    [
        pytest.param(_openai(ROLE), r"ends with data: \[DONE\]", id="no-done"),
        pytest.param(_openai(ROLE, "[DONE]", "[DONE]"), r"frame 1 is another", id="two-done"),
        pytest.param(
            _openai(_chunk({"content": "x"}), "[DONE]"),
            "frame 0: the first chunk does not name the role assistant",
            id="first-chunk-without-role",
        ),
        pytest.param(
            _openai(ROLE, _chunk({}, finish="stop"), _chunk({"content": "x"}), "[DONE]"),
            "frame 2: a choice after the finish chunk",
            id="choice-after-finish",
        ),
        pytest.param(
            _openai(ROLE, OPENAI_ERROR, _chunk({"content": "x"}), "[DONE]"),
            "frame 2: something other than",
            id="chunk-after-the-error",
        ),
        pytest.param(
            _openai(ROLE, _chunk(usage=USAGE), _chunk(usage=USAGE), "[DONE]"),
            "frame 2: usage is sent once",
            id="usage-twice",
        ),
        pytest.param(
            _openai(ROLE, _chunk({"content": "x"}, chunk_id="chatcmpl-other"), "[DONE]"),
            "one id per stream",
            id="two-ids",
        ),
        pytest.param(
            _openai(_chunk({"role": "assistant"}, chunk_id="other-1"), "[DONE]"),
            "does not start with 'chatcmpl-'",
            id="id-without-prefix",
        ),
    ],
)
def test_openai_reader_fails_naming_the_frame_when_the_stream_is_malformed(
    frames: Sequence[str], names: str
) -> None:
    """A malformed stream fails, and the message names the frame that is wrong."""
    with pytest.raises(AssertionError, match=names):
        openai_read_stream(200, NO_HEADERS, frames)


# --- Native frames ---

RESPONSE = Response(
    request_id=REQUEST_ID,
    trace_id="t",
    idempotency_key="k",
    agent=AGENT,
    agent_version="0.0.1",
    output={"text": "Hi"},
    metrics={"input_tokens": 7, "output_tokens": 5},
    status="ok",
    versions=Versions(chassis="0", config="c", prompt="p", model_route="r"),
)


def _event(event: Event) -> str:
    return _named(event.model_dump(mode="json"))


def _response_frame() -> str:
    return f"event: response\ndata: {RESPONSE.model_dump_json()}\n\n"


def test_native_reader_reads_the_answer_when_the_stream_ends_with_the_response() -> None:
    """The deltas join to the text; the envelope gives usage, status, and request id."""
    frames = [_event(Start(request_id=REQUEST_ID)), _event(Delta(text="Hi")), _response_frame()]

    read = native_read_stream(200, NO_HEADERS, frames)

    assert read == Readback(
        text="Hi",
        usage=Usage(7, 5),
        finish=None,
        status=200,
        error=None,
        run_status="ok",
        request_id=REQUEST_ID,
    )


@pytest.mark.parametrize(
    ("frames", "names"),
    [
        pytest.param([_event(Delta(text="Hi"))], "ends with the response frame", id="no-response"),
        pytest.param(
            [
                _event(Error(code="engine_error", message="m")),
                _event(Delta(text="x")),
                _response_frame(),
            ],
            "frame 1: a delta after the error",
            id="delta-after-error",
        ),
        pytest.param(
            [f"event: end\ndata: {Delta(text='x').model_dump_json()}\n\n", _response_frame()],
            "frame 0: name 'end' != event type 'delta'",
            id="name-is-not-the-type",
        ),
    ],
)
def test_native_reader_fails_naming_the_frame_when_the_stream_is_malformed(
    frames: Sequence[str], names: str
) -> None:
    """A malformed stream fails, and the message names the frame that is wrong."""
    with pytest.raises(AssertionError, match=names):
        native_read_stream(200, NO_HEADERS, frames)
