"""`chassis.core.inbound`: the types every inbound adapter shares, the status table (PoC-3 open
note, section 3), and `ReplyMeta.collect`, which gives every format the same totals.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from chassis.adapters.anthropic_compat.inbound import AnthropicInbound
from chassis.adapters.openai_compat.inbound import OpenAIInbound
from chassis.core.collector import collect
from chassis.core.envelope import Request, TaskInput, Versions
from chassis.core.events import Delta, End, Event, Metrics, Start
from chassis.core.inbound import (
    PUBLIC_MESSAGES,
    RUN_FAILED,
    Ids,
    InboundAdapter,
    Refused,
    Reply,
    ReplyMeta,
    Served,
    StreamReply,
    answer_text,
    public_message,
    rest_of_answer,
    status_for,
)
from chassis.server.interfaces.native import NativeInbound

VERSIONS = Versions(chassis="c", config="cfg", prompt="p", model_route="route")
SERVED = Served(agent="echo", agent_version="0.0.1", versions=VERSIONS)
IDS = Ids(request_id="r1", trace_id="t1", idempotency_key="i1")


@pytest.mark.parametrize(
    ("code", "retryable", "status"),
    [
        ("unsupported_parameter", False, 400),
        ("unsupported_message", False, 400),
        ("invalid_body", False, 400),
        ("model_not_found", False, 404),
        ("not_ready", True, 503),
        ("a2a.timeout", True, 504),
        ("engine_error", False, 500),
        ("engine_error", True, 500),
        ("a2a.transport", True, 503),
        ("budget_exhausted", True, 503),
        ("a2a.transport", False, 502),
        ("anything_else", False, 502),
        ("idempotency_conflict", False, 422),
        ("idempotency_in_progress", True, 409),
        ("state_unavailable", True, 503),
        ("state_unavailable", False, 503),
    ],
)
def test_status_for_follows_the_table(code: str, retryable: bool, status: int) -> None:
    assert status_for(code, retryable) == status


@pytest.mark.parametrize(
    "code", ["idempotency_conflict", "idempotency_in_progress", "state_unavailable"]
)
def test_the_idempotency_codes_have_a_fixed_public_message(code: str) -> None:
    assert code in PUBLIC_MESSAGES
    assert public_message(code) == PUBLIC_MESSAGES[code] != RUN_FAILED


def test_refused_carries_its_reply() -> None:
    refused = Refused(400, {"x-should-retry": "false"}, {"detail": "no"})
    assert isinstance(refused, Exception)
    assert (refused.status, refused.headers, refused.body) == (
        400,
        {"x-should-retry": "false"},
        {"detail": "no"},
    )
    assert refused.reply == Reply(400, {"x-should-retry": "false"}, {"detail": "no"})


def _request(request_id: str = "r2", trace_id: str = "t2") -> Request:
    return Request(
        request_id=request_id,
        trace_id=trace_id,
        idempotency_key="i2",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="x"),
    )


def test_ids_of_a_request_and_meta_for_a_request() -> None:
    request = _request()
    assert Ids.of(request) == Ids(request_id="r2", trace_id="t2", idempotency_key="i2")
    meta = ReplyMeta(ids=IDS, served=SERVED, created=7)
    assert meta.request is None and dict(meta.options) == {}
    bound = meta.for_request(request)
    assert bound.request is request and bound.ids == Ids.of(request)
    assert (bound.served, bound.created) == (SERVED, 7)
    assert meta.request is None, "ReplyMeta is immutable; for_request returns a copy"


async def _aiter(events: Sequence[Event]) -> AsyncIterator[Event]:
    for event in events:
        yield event


async def test_meta_collect_gives_what_core_collect_gives() -> None:
    events: list[Event] = [
        Start(request_id="r2"),
        Delta(text="a "),
        Metrics(input_tokens=3, output_tokens=2),
        Delta(text="b"),
        Metrics(input_tokens=1, output_tokens=1, attempt=2),
        End(status="retry"),
    ]
    request = _request()
    meta = ReplyMeta(ids=IDS, served=SERVED, created=0).for_request(request)
    response = await meta.collect(events)
    assert response == await collect(_aiter(events), request, VERSIONS)
    assert response.metrics["input_tokens"] == 4 and response.output == {"text": "a b"}


async def test_meta_collect_needs_the_request() -> None:
    with pytest.raises(ValueError, match="request"):
        await ReplyMeta(ids=IDS, served=SERVED, created=0).collect([])


def test_native_inbound_is_an_inbound_adapter() -> None:
    adapter = NativeInbound()
    assert isinstance(adapter, InboundAdapter)
    assert adapter.interface == "native"
    assert NativeInbound(interface="mcp").interface == "mcp"


async def test_stream_reply_defaults_to_sse_with_no_headers() -> None:
    async def frames() -> AsyncIterator[str]:
        yield "data: x\n\n"

    reply = StreamReply(frames())
    assert reply.media_type == "text/event-stream" and dict(reply.headers) == {}
    assert [f async for f in reply.frames] == ["data: x\n\n"]


# --- the answer text, one rule for every chat format -------------------------------------------


@pytest.mark.parametrize(
    ("output", "text"),
    [
        ({"text": "t", "tool_calls": [{"name": "x"}]}, "t"),
        ({"text": ""}, ""),
        ({"b": 1, "a": [1, 2]}, '{"a":[1,2],"b":1}'),
        ({"text": None, "x": 1}, '{"text":null,"x":1}'),
        ({"text": 5}, '{"text":5}'),
        (None, ""),
    ],
)
def test_answer_text_is_text_else_compact_sorted_json(
    output: dict[str, Any] | None, text: str
) -> None:
    assert answer_text(output) == text


@pytest.mark.parametrize(
    ("output", "streamed", "rest"),
    [
        ({"text": "abcd"}, "ab", "cd"),
        ({"text": "abcd"}, "", "abcd"),
        ({"a": 1}, "", '{"a":1}'),
        ({"text": "abcd"}, "abcd", ""),
        ({"text": "ab"}, "abcd", ""),
        ({"text": "Hi"}, "Hello", ""),
        ({"label": "x"}, "thinking", ""),
        ({"text": ""}, "ab", ""),
    ],
)
def test_rest_of_answer_is_what_the_deltas_left_out_else_nothing(
    output: dict[str, Any], streamed: str, rest: str
) -> None:
    """Sent text stands: the rest when the answer extends the deltas, else nothing more."""
    assert rest_of_answer(output, streamed) == rest


def _openai_texts(frames: list[str]) -> list[str]:
    texts = []
    for frame in frames:
        data = frame.removeprefix("data: ").strip()
        if data == "[DONE]":
            continue
        for choice in json.loads(data)["choices"]:
            if choice["delta"].get("content"):
                texts.append(choice["delta"]["content"])
    return texts


def _anthropic_texts(frames: list[str]) -> list[str]:
    texts = []
    for frame in frames:
        name, data = frame.strip().split("\n")
        if name == "event: content_block_delta":
            texts.append(json.loads(data.removeprefix("data: "))["delta"]["text"])
    return texts


@pytest.mark.parametrize(
    ("deltas", "output", "texts"),
    [
        (["ab"], {"text": "abcd"}, ["ab", "cd"]),
        (["ab", "cd"], {"text": "abcd"}, ["ab", "cd"]),
        ([], {"b": 2, "a": 1}, ['{"a":1,"b":2}']),
        (["Hel", "lo"], {"text": "Hi"}, ["Hel", "lo"]),
        (["thinking"], {"label": "x"}, ["thinking"]),
    ],
)
@pytest.mark.parametrize("fmt", ["openai", "anthropic"])
async def test_both_chat_formats_stream_the_same_text(
    fmt: str, deltas: list[str], output: dict[str, Any], texts: list[str]
) -> None:
    """OpenAI and Anthropic send the same text chunks for the same events, including deltas
    that are not a prefix of the final answer: those stand, and nothing more is sent.
    """
    events: list[Event] = [Start(request_id="r2"), *(Delta(text=d) for d in deltas)]
    events.append(End(output=output))
    meta = ReplyMeta(ids=IDS, served=SERVED, created=0).for_request(_request())
    adapter: InboundAdapter[Any] = OpenAIInbound() if fmt == "openai" else AnthropicInbound()
    frames = [frame async for frame in adapter.stream(_aiter(events), meta).frames]
    read = _openai_texts if fmt == "openai" else _anthropic_texts
    assert read(frames) == texts
