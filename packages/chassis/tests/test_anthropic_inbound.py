"""`chassis.adapters.anthropic_compat`: the Anthropic Messages format onto the canonical request and
back, pure (PoC-3 open note, sections 2 and 3). No app, no engine: `to_request` over plain bodies,
and the back-mapping read with the official `anthropic` types.

Exit criterion: "The OpenAI and Anthropic SDKs work with only a base URL change" (the mapping half)
and "Any gaps between the formats are listed" (the refuse and ignore rules).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any

import pytest
from anthropic.types import (
    ErrorResponse,
    Message,
    MessageParam,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
)
from chassis.adapters.anthropic_compat.inbound import AnthropicInbound
from chassis.core.envelope import Budget, Request, Response, TaskInput, Versions
from chassis.core.events import Delta, End, Error, Event, Metrics, Start, ToolCall
from chassis.core.inbound import Ids, InboundAdapter, Refused, ReplyMeta, Served, public_message
from pydantic import TypeAdapter

VERSIONS = Versions(chassis="c", config="cfg", prompt="p", model_route="route")
SERVED = Served(agent="echo", agent_version="0.0.1", versions=VERSIONS)
IDS = Ids(request_id="r1", trace_id="t1", idempotency_key="i1")
ADAPTER = AnthropicInbound()


def _body(**extra: Any) -> dict[str, Any]:
    return {
        "model": "echo",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
        **extra,
    }


def _map(body: dict[str, Any], headers: dict[str, str] | None = None) -> Request:
    return ADAPTER.to_request(body, headers or {}, ids=IDS, served=SERVED)


def _refused(body: dict[str, Any]) -> Refused:
    with pytest.raises(Refused) as info:
        _map(body)
    return info.value


def _error_body(refused: Refused) -> ErrorResponse:
    return ErrorResponse.model_validate(refused.body)


# --- to_request (section 2) --------------------------------------------------------------------


def test_is_an_inbound_adapter_for_anthropic() -> None:
    assert isinstance(ADAPTER, InboundAdapter)
    assert ADAPTER.interface == "anthropic"


def test_one_user_turn_is_the_plain_native_request() -> None:
    request = _map(_body())
    assert request == Request(
        request_id="r1",
        trace_id="t1",
        idempotency_key="i1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="hello", data={}),
        context_ref=None,
        stream=False,
        budget=Budget(max_tokens=64),
    )


def test_system_history_and_text_blocks_map_onto_input() -> None:
    body = _body(
        system=[{"type": "text", "text": "be brief"}, {"type": "text", "text": "be kind"}],
        stream=True,
        messages=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "b"},
                ],
            },
        ],
    )
    request = _map(body)
    assert request.input == TaskInput(
        text="a\nb",
        data={
            "system": "be brief\nbe kind",
            "history": [
                {"role": "user", "text": "first"},
                {"role": "assistant", "text": "reply"},
            ],
        },
    )
    assert request.stream is True and request.budget == Budget(max_tokens=64)


def test_a_system_string_and_system_role_messages_join_in_order() -> None:
    body = _body(
        system="top",
        messages=[{"role": "system", "content": "inline"}, {"role": "user", "content": "q"}],
    )
    assert _map(body).input == TaskInput(text="q", data={"system": "top\ninline"})


def test_empty_system_is_not_set() -> None:
    assert _map(_body(system="")).input.data == {}
    assert _map(_body(system=[])).input.data == {}


def test_ids_and_agent_are_the_given_ones_not_the_body_or_headers() -> None:
    headers = {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01", "idempotency-key": "k"}
    request = _map(_body(metadata={"user_id": "u-1"}), headers)
    assert Ids.of(request) == IDS
    assert (request.agent, request.agent_version) == ("echo", "0.0.1")


def test_to_request_is_pure() -> None:
    body = _body(
        system="s", messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}]
    )
    snapshot = json.dumps(body, sort_keys=True)
    assert _map(body) == _map(body)
    assert json.dumps(body, sort_keys=True) == snapshot


def test_lazy_iterators_are_read_like_lists() -> None:
    """FastAPI hands `messages` and `system` over as lazy iterators."""
    body = _body(
        system=iter([{"type": "text", "text": "s"}]),
        messages=iter([{"role": "user", "content": iter([{"type": "text", "text": "x"}])}]),
    )
    assert _map(body).input == TaskInput(text="x", data={"system": "s"})


def test_a_validation_error_inside_a_lazy_iterator_is_a_400() -> None:
    """The lazy iterator FastAPI gives validates each item only when it is read."""
    lazy: Iterable[MessageParam] = TypeAdapter(Iterable[MessageParam]).validate_python(
        [{"role": "user", "content": "x"}, {"role": "robot", "content": "y"}]
    )
    refused = _refused(_body(messages=lazy))
    assert refused.status == 400
    assert _error_body(refused).error.type == "invalid_request_error"
    message = refused.body["error"]["message"]
    assert message.startswith("invalid_body: messages")
    assert "robot" not in message, "the input is not echoed"
    assert refused.headers["x-should-retry"] == "false"


def test_a_model_that_is_not_the_agent_is_404_not_found_error() -> None:
    refused = _refused(_body(model="claude-opus-4"))
    assert refused.status == 404
    error = _error_body(refused)
    assert error.type == "error" and error.error.type == "not_found_error"
    assert error.error.message.startswith("model_not_found: ")
    assert "claude-opus-4" in error.error.message and "'echo'" in error.error.message
    assert error.request_id == "r1"
    assert refused.headers["x-should-retry"] == "false"


IMAGE = {"type": "image", "source": {"type": "url", "url": "https://example.invalid/a.png"}}
TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "f", "input": {}}
TOOL_RESULT = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}
THINKING = {"type": "thinking", "thinking": "hm", "signature": "sig"}
REDACTED = {"type": "redacted_thinking", "data": "x"}
DOCUMENT = {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "d"}}

REFUSED: list[tuple[str, dict[str, Any], str]] = [
    ("tools", _body(tools=[{"name": "f", "input_schema": {"type": "object"}}]), "tools"),
    ("tool_choice any", _body(tool_choice={"type": "any"}), "tool_choice"),
    ("tool_choice tool", _body(tool_choice={"type": "tool", "name": "f"}), "tool_choice"),
    ("mcp_servers", _body(mcp_servers=[{"type": "url", "url": "u", "name": "n"}]), "mcp_servers"),
    ("container", _body(container="c-1"), "container"),
    (
        "output_config.format",
        _body(output_config={"format": {"type": "json_schema", "schema": {}}}),
        "output_config.format",
    ),
    ("image", _body(messages=[{"role": "user", "content": [IMAGE]}]), "messages[0].content"),
    ("document", _body(messages=[{"role": "user", "content": [DOCUMENT]}]), "messages[0]"),
    (
        "tool_use in history",
        _body(
            messages=[
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": [TOOL_USE]},
                {"role": "user", "content": [TOOL_RESULT]},
            ]
        ),
        "messages[1].content",
    ),
    (
        "tool_result",
        _body(messages=[{"role": "user", "content": [TOOL_RESULT]}]),
        "messages[0].content",
    ),
    (
        "thinking in history",
        _body(
            messages=[
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": [THINKING, {"type": "text", "text": "a"}]},
                {"role": "user", "content": "q2"},
            ]
        ),
        "messages[1].content",
    ),
    (
        "redacted_thinking in history",
        _body(
            messages=[
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": [REDACTED]},
                {"role": "user", "content": "q2"},
            ]
        ),
        "messages[1].content",
    ),
    (
        "assistant prefill",
        _body(messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "The"}]),
        "messages[1].role",
    ),
    ("system last", _body(messages=[{"role": "system", "content": "s"}]), "messages[0].role"),
    ("no messages", _body(messages=[]), "messages"),
    ("system image block", _body(system=[IMAGE]), "system"),
]


@pytest.mark.parametrize(
    ("body", "param"), [(b, p) for _, b, p in REFUSED], ids=[n for n, _, _ in REFUSED]
)
def test_refused_bodies_are_400_in_the_anthropic_error_shape(
    body: dict[str, Any], param: str
) -> None:
    refused = _refused(body)
    assert refused.status == 400
    error = _error_body(refused)
    assert error.type == "error" and error.error.type == "invalid_request_error"
    assert param in error.error.message
    assert error.error.message.split(":", 1)[0] in {"unsupported_parameter", "unsupported_message"}
    assert error.request_id == "r1"
    assert refused.headers["x-should-retry"] == "false"
    assert refused.headers["x-request-id"] == "r1"


@pytest.mark.parametrize(
    "extra",
    [
        {"tools": []},
        {"tool_choice": {"type": "auto"}},
        {"tool_choice": {"type": "none"}},
        {"mcp_servers": []},
        {"container": None},
        {"output_config": {"effort": "low"}},
        {"output_config": {"format": None}},
    ],
)
def test_empty_or_harmless_forms_of_refused_params_are_accepted(extra: dict[str, Any]) -> None:
    assert _map(_body(**extra)) == _map(_body())


IGNORED_BODY = _body(
    temperature=0.2,
    top_p=0.9,
    top_k=5,
    stop_sequences=["END"],
    service_tier="auto",
    thinking={"type": "enabled", "budget_tokens": 1024},
    cache_control={"type": "ephemeral"},
    inference_geo="us",
    tool_choice={"type": "auto"},
    output_config={"effort": "high"},
    metadata={"user_id": "u-1"},
    user_profile_id="up-1",
)


def test_ignored_parameters_do_not_change_the_request_and_are_reported() -> None:
    headers = {"anthropic-version": "2023-06-01", "anthropic-beta": "x", "x-api-key": "sk-no"}
    assert _map(IGNORED_BODY, headers) == _map(_body())
    assert ADAPTER.ignored(IGNORED_BODY) == [
        "cache_control",
        "inference_geo",
        "output_config.effort",
        "service_tier",
        "stop_sequences",
        "temperature",
        "thinking",
        "tool_choice",
        "top_k",
        "top_p",
    ], "metadata and user_profile_id are dropped, never counted (personal data)"
    assert ADAPTER.ignored(_body()) == []
    assert ADAPTER.ignored(_body(temperature=None)) == []


# --- back-mapping (section 3) ------------------------------------------------------------------


def _request(stream: bool = False) -> Request:
    return Request(
        request_id="r1",
        trace_id="t1",
        idempotency_key="i1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="hello"),
        stream=stream,
    )


def _meta(stream: bool = False) -> ReplyMeta:
    return ReplyMeta(ids=IDS, served=SERVED, created=100).for_request(_request(stream))


def _response(
    output: dict[str, Any], status: str = "ok", tokens: tuple[int, int] = (7, 3)
) -> Response:
    return Response(
        request_id="r1",
        trace_id="t1",
        idempotency_key="i1",
        agent="echo",
        agent_version="0.0.1",
        status=status,
        output=output,
        metrics={"input_tokens": tokens[0], "output_tokens": tokens[1], "attempts": 1},
        versions=VERSIONS,
    )


def test_complete_is_an_official_message_with_usage_and_headers() -> None:
    reply = ADAPTER.complete(_response({"text": "hi there"}), _meta())
    assert reply.status == 200
    message = Message.model_validate(reply.body)
    assert message.id == "msg_r1" and message.type == "message" and message.role == "assistant"
    assert message.model == "echo"
    assert [(b.type, getattr(b, "text", None)) for b in message.content] == [("text", "hi there")]
    assert (message.stop_reason, message.stop_sequence) == ("end_turn", None)
    assert (message.usage.input_tokens, message.usage.output_tokens) == (7, 3)
    assert message.usage.cache_read_input_tokens is None
    assert message.usage.cache_creation_input_tokens is None
    assert dict(reply.headers) == {
        "request-id": "r1",
        "x-request-id": "r1",
        "x-trace-id": "t1",
        "x-chassis-status": "ok",
    }


@pytest.mark.parametrize("status", ["retry", "fallback"])
def test_retry_and_fallback_are_a_normal_answer_with_the_status_header(status: str) -> None:
    reply = ADAPTER.complete(_response({"text": "x"}, status=status), _meta())
    assert reply.status == 200 and reply.headers["x-chassis-status"] == status


def test_no_text_is_empty_content_and_structured_output_is_compact_json() -> None:
    empty = Message.model_validate(ADAPTER.complete(_response({"text": ""}), _meta()).body)
    assert empty.content == []
    data = {"b": 1, "a": [1, 2]}
    structured = Message.model_validate(ADAPTER.complete(_response(data), _meta()).body)
    assert [getattr(b, "text", None) for b in structured.content] == ['{"a":[1,2],"b":1}']


async def _aiter(events: Sequence[Event]) -> AsyncIterator[Event]:
    for event in events:
        yield event


def _parse_sse(frames: Sequence[str]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for frame in frames:
        assert frame.endswith("\n\n"), frame
        lines = frame.strip("\n").split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ")
        out.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return out


async def _stream(events: Sequence[Event]) -> tuple[dict[str, str], list[tuple[str, Any]]]:
    reply = ADAPTER.stream(_aiter(events), _meta(stream=True))
    assert reply.media_type == "text/event-stream"
    frames = [f async for f in reply.frames]
    return dict(reply.headers), _parse_sse(frames)


async def test_stream_is_the_official_event_sequence() -> None:
    headers, frames = await _stream(
        [
            Start(request_id="r1"),
            Metrics(input_tokens=4, output_tokens=1),
            Delta(text="one "),
            ToolCall(call_id="c1", name="glossary_lookup", arguments={"term": "x"}),
            Metrics(input_tokens=2, output_tokens=2, attempt=1),
            Delta(text="two"),
            End(status="ok"),
        ]
    )
    assert headers == {"request-id": "r1", "x-request-id": "r1", "x-trace-id": "t1"}
    assert [name for name, _ in frames] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    for name, data in frames:
        assert data["type"] == name
    start = RawMessageStartEvent.model_validate(frames[0][1])
    assert start.message.id == "msg_r1" and start.message.content == []
    assert start.message.stop_reason is None
    assert (start.message.usage.input_tokens, start.message.usage.output_tokens) == (0, 0)
    block = RawContentBlockStartEvent.model_validate(frames[1][1])
    assert block.index == 0 and block.content_block.type == "text"
    deltas = [RawContentBlockDeltaEvent.model_validate(d) for _, d in frames[2:4]]
    assert [getattr(d.delta, "text", None) for d in deltas] == ["one ", "two"]
    assert all(d.delta.type == "text_delta" and d.index == 0 for d in deltas)
    assert frames[4][1] == {"type": "content_block_stop", "index": 0}
    delta = RawMessageDeltaEvent.model_validate(frames[5][1])
    assert (delta.delta.stop_reason, delta.delta.stop_sequence) == ("end_turn", None)
    assert (delta.usage.input_tokens, delta.usage.output_tokens) == (6, 3)
    assert frames[6][1] == {"type": "message_stop"}
    dumped = json.dumps(frames)
    assert '"tool_use"' not in dumped and "glossary_lookup" not in dumped, (
        "the agent's tool calls are never client tool calls"
    )


async def test_stream_without_text_opens_no_block() -> None:
    _, frames = await _stream([Start(request_id="r1"), End(output={"text": ""})])
    assert [name for name, _ in frames] == ["message_start", "message_delta", "message_stop"]


async def test_stream_of_structured_output_sends_the_json_text_once() -> None:
    _, frames = await _stream([Start(request_id="r1"), End(output={"b": 2, "a": 1})])
    texts = [d["delta"]["text"] for n, d in frames if n == "content_block_delta"]
    assert texts == ['{"a":1,"b":2}']
    assert [n for n, _ in frames].count("content_block_stop") == 1


async def test_stream_sends_the_rest_of_an_end_text_that_extends_the_deltas() -> None:
    _, frames = await _stream([Delta(text="ab"), End(output={"text": "abcd"})])
    assert [d["delta"]["text"] for n, d in frames if n == "content_block_delta"] == ["ab", "cd"]


async def test_error_mid_stream_is_an_error_event_and_then_the_end() -> None:
    _, frames = await _stream(
        [
            Start(request_id="r1"),
            Delta(text="part"),
            Error(code="a2a.timeout", message="slow", retryable=True),
            Delta(text="never"),
        ]
    )
    assert [name for name, _ in frames] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "error",
    ]
    error = ErrorResponse.model_validate(frames[-1][1])
    assert error.error.type == "timeout_error"
    assert error.error.message == f"a2a.timeout: {public_message('a2a.timeout')}"
    assert "part" not in error.error.message, "text that streamed is not repeated"


@pytest.mark.parametrize(
    ("code", "retryable", "status", "kind", "should_retry"),
    [
        ("invalid_body", False, 400, "invalid_request_error", "false"),
        ("model_not_found", False, 404, "not_found_error", "false"),
        ("not_ready", True, 503, "overloaded_error", "true"),
        ("a2a.timeout", True, 504, "timeout_error", "true"),
        ("engine_error", False, 500, "api_error", "false"),
        ("a2a.transport", True, 503, "overloaded_error", "true"),
        ("bad_output", False, 502, "api_error", "false"),
    ],
)
def test_error_status_type_and_should_retry_per_code(
    code: str, retryable: bool, status: int, kind: str, should_retry: str
) -> None:
    meta = ReplyMeta(ids=IDS, served=SERVED, created=0)
    reply = ADAPTER.error(code, "why", retryable, meta)
    assert reply.status == status
    error = ErrorResponse.model_validate(reply.body)
    assert (error.type, error.error.type, error.request_id) == ("error", kind, "r1")
    assert error.error.message == f"{code}: why"
    assert reply.headers["x-should-retry"] == should_retry
    assert (reply.headers["x-request-id"], reply.headers["x-trace-id"]) == ("r1", "t1")
