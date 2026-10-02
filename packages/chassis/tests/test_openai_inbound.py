"""`chassis.adapters.openai_compat.inbound`: the OpenAI interface's pure mapping (PoC-3 open note,
sections 2 and 3). No app, no engine: the body onto the canonical `Request`, and the run's answer
back into the official `openai` SDK shapes (`ChatCompletion`, `ChatCompletionChunk`, the error
body). Exit criterion: "The OpenAI and Anthropic SDKs work with only a base URL change".
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from chassis.adapters.openai_compat.inbound import OpenAIInbound
from chassis.core.envelope import Budget, Request, Response, TaskInput, Versions
from chassis.core.events import Delta, End, Error, Event, Metrics, Start, ToolCall
from chassis.core.inbound import Ids, InboundAdapter, Refused, ReplyMeta, Served
from openai.types.chat import ChatCompletion, ChatCompletionChunk, CompletionCreateParams
from openai.types.shared import ErrorObject
from pydantic import TypeAdapter

VERSIONS = Versions(chassis="c", config="cfg", prompt="p", model_route="route")
SERVED = Served(agent="echo", agent_version="0.0.1", versions=VERSIONS)
IDS = Ids(request_id="r1", trace_id="t1", idempotency_key="i1")
ADAPTER = OpenAIInbound()
USER = {"role": "user", "content": "hello"}


def _body(**extra: Any) -> dict[str, Any]:
    return {"model": "echo", "messages": [USER], **extra}


def _map(body: Any, headers: dict[str, str] | None = None) -> Request:
    return ADAPTER.to_request(body, headers or {}, ids=IDS, served=SERVED)


def _refused(body: Any) -> Refused:
    with pytest.raises(Refused) as caught:
        _map(body)
    return caught.value


def _meta(request: Request | None = None, **options: object) -> ReplyMeta:
    meta = ReplyMeta(IDS, SERVED, 1_700_000_000, options=options)
    return meta.for_request(request or _map(_body()))


# --- to_request (section 2) ---------------------------------------------------------------------


def test_openai_inbound_is_an_inbound_adapter() -> None:
    assert isinstance(ADAPTER, InboundAdapter)
    assert ADAPTER.interface == "openai"


def test_one_user_turn_is_the_plain_canonical_request() -> None:
    request = _map(_body())
    assert request == Request(
        request_id="r1",
        trace_id="t1",
        idempotency_key="i1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="hello", data={}),
        stream=False,
        budget=Budget(),
    )
    assert request.budget.max_tokens == 2000 and request.context_ref is None


def test_system_developer_and_history_are_carried_in_data() -> None:
    body = _body(
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "first"},
            {"role": "developer", "content": [{"type": "text", "text": "dev"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": "last"}, {"type": "text", "text": "x"}],
            },
        ],
        stream=True,
    )
    request = _map(body)
    assert request.input.text == "last\nx"
    assert request.input.data == {
        "system": "be brief\ndev",
        "history": [
            {"role": "user", "text": "first"},
            {"role": "assistant", "text": "a\nb"},
        ],
    }
    assert request.stream is True


def test_to_request_is_pure_and_leaves_the_body_alone() -> None:
    body = _body(messages=[{"role": "system", "content": "s"}, USER], max_tokens=9)
    before = copy.deepcopy(body)
    assert _map(body) == _map(body)
    assert body == before


def test_ids_are_the_ones_given_never_from_headers() -> None:
    headers = {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
    request = _map(_body(), headers)
    assert (request.request_id, request.trace_id, request.idempotency_key) == ("r1", "t1", "i1")


def test_a_model_that_is_not_the_agent_is_404_model_not_found() -> None:
    refused = _refused(_body(model="gpt-4o"))
    assert refused.status == 404
    assert refused.headers["x-should-retry"] == "false"
    error = ErrorObject.model_validate(refused.body["error"])
    assert (error.type, error.code, error.param) == (
        "invalid_request_error",
        "model_not_found",
        "model",
    )
    assert "gpt-4o" in error.message and "echo" in error.message
    assert refused.body["error"]["retryable"] is False


@pytest.mark.parametrize(
    ("extra", "max_tokens"),
    [
        ({}, 2000),
        ({"max_tokens": 7}, 7),
        ({"max_completion_tokens": 8}, 8),
        ({"max_completion_tokens": 8, "max_tokens": 8}, 8),
        ({"max_completion_tokens": None, "max_tokens": 5}, 5),
    ],
)
def test_max_completion_tokens_else_max_tokens_is_the_budget(
    extra: dict[str, Any], max_tokens: int
) -> None:
    request = _map(_body(**extra))
    assert request.budget == Budget(max_tokens=max_tokens)


@pytest.mark.parametrize(
    "extra",
    [
        {"max_completion_tokens": 8, "max_tokens": 9},
        {"max_tokens": 0},
        {"max_completion_tokens": -1},
    ],
)
def test_conflicting_or_non_positive_token_limits_are_400(extra: dict[str, Any]) -> None:
    refused = _refused(_body(**extra))
    assert refused.status == 400
    assert refused.body["error"]["code"] == "invalid_body"
    assert refused.body["error"]["param"] in {"max_tokens", "max_completion_tokens"}


TOOL = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
IMAGE = {"type": "image_url", "image_url": {"url": "https://example.invalid/x.png"}}
CALL = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}

REFUSED_PARAMS: list[tuple[str, dict[str, Any]]] = [
    ("n", {"n": 2}),
    ("logprobs", {"logprobs": True}),
    ("top_logprobs", {"top_logprobs": 3}),
    ("tools", {"tools": [TOOL]}),
    ("functions", {"functions": [{"name": "f"}]}),
    ("tool_choice", {"tool_choice": "required"}),
    ("tool_choice", {"tool_choice": {"type": "function", "function": {"name": "f"}}}),
    ("function_call", {"function_call": {"name": "f"}}),
    ("response_format", {"response_format": {"type": "json_object"}}),
    (
        "response_format",
        {"response_format": {"type": "json_schema", "json_schema": {"name": "x"}}},
    ),
    ("audio", {"audio": {"voice": "alloy", "format": "mp3"}}),
    ("modalities", {"modalities": ["text", "audio"]}),
    ("web_search_options", {"web_search_options": {}}),
    ("moderation", {"moderation": {"model": "omni-moderation-latest"}}),
]


@pytest.mark.parametrize(("param", "extra"), REFUSED_PARAMS, ids=[p for p, _ in REFUSED_PARAMS])
def test_refused_parameters_are_400_unsupported_parameter(
    param: str, extra: dict[str, Any]
) -> None:
    refused = _refused(_body(**extra))
    assert refused.status == 400 and refused.headers["x-should-retry"] == "false"
    error = refused.body["error"]
    assert ErrorObject.model_validate(error).type == "invalid_request_error"
    assert (error["code"], error["param"], error["retryable"]) == (
        "unsupported_parameter",
        param,
        False,
    )


REFUSED_MESSAGES: list[tuple[str, list[dict[str, Any]]]] = [
    ("messages", []),
    ("messages[0].content", [{"role": "user", "content": [IMAGE]}]),
    (
        "messages[0].content",
        [{"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}],
    ),
    ("messages[0].content", [{"role": "user", "content": [{"type": "file", "file": {}}]}]),
    ("messages[0].content", [{"role": "system", "content": [IMAGE]}, USER]),
    (
        "messages[0].tool_calls",
        [{"role": "assistant", "content": None, "tool_calls": [CALL]}, USER],
    ),
    ("messages[0].role", [{"role": "tool", "content": "r", "tool_call_id": "c1"}, USER]),
    ("messages[0].role", [{"role": "function", "content": "r", "name": "f"}, USER]),
    ("messages[1].role", [USER, {"role": "assistant", "content": "prefill"}]),
    ("messages[1].role", [USER, {"role": "system", "content": "late"}]),
    ("messages[0].audio", [{"role": "assistant", "content": "a", "audio": {"id": "x"}}, USER]),
    ("messages[0].content", [{"role": "assistant", "content": None}, USER]),
    (
        "messages[0].content",
        [{"role": "assistant", "content": [{"type": "refusal", "refusal": "no"}]}, USER],
    ),
]


@pytest.mark.parametrize(
    ("param", "messages"),
    REFUSED_MESSAGES,
    ids=[f"{i}-{p}" for i, (p, _) in enumerate(REFUSED_MESSAGES)],
)
def test_refused_messages_are_400_with_the_message_named(
    param: str, messages: list[dict[str, Any]]
) -> None:
    refused = _refused(_body(messages=messages))
    assert refused.status == 400
    error = refused.body["error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] in {"unsupported_message", "invalid_body"}
    assert error["param"] == param


def test_echoed_empty_fields_and_harmless_values_are_accepted() -> None:
    plain = _map(_body())
    harmless = _body(
        n=1,
        logprobs=False,
        tools=[],
        tool_choice="auto",
        function_call="none",
        response_format={"type": "text"},
        modalities=["text"],
        messages=[
            {"role": "assistant", "content": "a", "tool_calls": [], "refusal": None, "name": "bot"},
            {"role": "user", "content": "hello", "name": "me"},
        ],
    )
    request = _map(harmless)
    assert request.input == TaskInput(
        text="hello", data={"history": [{"role": "assistant", "text": "a"}]}
    )
    assert request.model_copy(update={"input": plain.input}) == plain


IGNORED_BODY: dict[str, Any] = {
    "temperature": 0.2,
    "top_p": 0.9,
    "seed": 1,
    "stop": ["x"],
    "frequency_penalty": 0.1,
    "presence_penalty": 0.1,
    "logit_bias": {"1": 1},
    "store": False,
    "service_tier": "auto",
    "reasoning_effort": "low",
    "prediction": {"type": "content", "content": "x"},
    "verbosity": "low",
    "parallel_tool_calls": True,
    "prompt_cache_key": "k",
    "prompt_cache_retention": "24h",
    "stream_options": {"include_obfuscation": False},
}


def test_ignored_parameters_do_not_change_the_canonical_request_and_are_named() -> None:
    body = _body(**IGNORED_BODY, metadata={"k": "v"}, user="u-1", safety_identifier="s")
    assert _map(body) == _map(_body())
    names = ADAPTER.ignored(body, {"authorization": "Bearer placeholder"})
    assert names == [*IGNORED_BODY, "authorization"]
    assert not {"metadata", "user", "safety_identifier"} & set(names), (
        "personal data is dropped, not counted"
    )
    assert (
        ADAPTER.ignored(_body(temperature=None, stream_options={"include_usage": True}), {}) == []
    )


def test_options_read_include_usage_only_when_streaming() -> None:
    assert ADAPTER.options(_body(stream=True, stream_options={"include_usage": True})) == {
        "include_usage": True
    }
    assert ADAPTER.options(_body(stream_options={"include_usage": True})) == {
        "include_usage": False
    }
    assert ADAPTER.options(_body(stream=True)) == {"include_usage": False}


PARAMS: TypeAdapter[Any] = TypeAdapter(CompletionCreateParams)
"""Kept alive for the module: a lazy iterator read after its `TypeAdapter` is freed panics in
pydantic-core (FastAPI keeps its own adapter for the life of the app)."""


def _validated(body: dict[str, Any]) -> Any:
    """The body as FastAPI hands it over: `messages` and every nested list are lazy iterators."""
    return PARAMS.validate_python(body)


def test_a_lazy_body_maps_like_a_plain_one() -> None:
    body = _body(messages=[{"role": "system", "content": [{"type": "text", "text": "s"}]}, USER])
    assert _map(_validated(body)) == _map(body)


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "bogus", "content": "x"}],
        [{"role": "user"}],
        [{"role": "user", "content": [{"x": 1}]}],
    ],
)
def test_a_validation_error_in_the_lazy_messages_is_400_invalid_body(
    messages: list[dict[str, Any]],
) -> None:
    refused = _refused(_validated(_body(messages=messages)))
    assert refused.status == 400
    error = refused.body["error"]
    assert (error["code"], error["param"]) == ("invalid_body", "messages")
    assert "bogus" not in error["message"], "the input is not echoed"


# --- back-mapping (section 3) -------------------------------------------------------------------


def _response(output: dict[str, Any], status: Any = "ok") -> Response:
    return Response(
        request_id="r1",
        trace_id="t1",
        idempotency_key="i1",
        agent="echo",
        agent_version="0.0.1",
        output=output,
        metrics={"input_tokens": 4, "output_tokens": 3, "attempts": 1},
        status=status,
        versions=VERSIONS,
    )


def test_complete_is_an_official_chat_completion() -> None:
    reply = ADAPTER.complete(_response({"text": "hi there"}), _meta())
    assert reply.status == 200
    assert dict(reply.headers) == {
        "x-request-id": "r1",
        "x-trace-id": "t1",
        "x-chassis-status": "ok",
    }
    completion = ChatCompletion.model_validate(reply.body)
    assert completion.id == "chatcmpl-r1" and completion.object == "chat.completion"
    assert (completion.created, completion.model) == (1_700_000_000, "echo")
    [choice] = completion.choices
    assert (choice.index, choice.finish_reason) == (0, "stop")
    assert (choice.message.role, choice.message.content) == ("assistant", "hi there")
    assert choice.message.tool_calls is None
    usage = completion.usage
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (4, 3, 7)


@pytest.mark.parametrize(
    ("output", "text"),
    [
        ({"text": "t", "tool_calls": [{"name": "x"}]}, "t"),
        ({"text": ""}, ""),
        ({"b": 1, "a": [1, 2]}, '{"a":[1,2],"b":1}'),
        ({"text": None, "x": 1}, '{"text":null,"x":1}'),
    ],
)
def test_complete_content_is_the_answer_text(output: dict[str, Any], text: str) -> None:
    """The rule itself is tested once, in `test_inbound.py` (`chassis.core.inbound`)."""
    body = ADAPTER.complete(_response(output), _meta()).body
    assert ChatCompletion.model_validate(body).choices[0].message.content == text


@pytest.mark.parametrize("status", ["retry", "fallback"])
def test_retry_and_fallback_are_a_normal_answer_with_the_status_header(status: str) -> None:
    reply = ADAPTER.complete(_response({"text": "ok"}, status), _meta())
    assert reply.status == 200 and reply.headers["x-chassis-status"] == status


def test_complete_of_a_run_without_an_end_event_is_an_http_error() -> None:
    reply = ADAPTER.complete(_response({"text": "partial"}, "error"), _meta())
    assert reply.status == 500 and reply.body["error"]["code"] == "engine_error"


async def _aiter(events: Sequence[Event]) -> AsyncIterator[Event]:
    for event in events:
        yield event


async def _frames(events: Sequence[Event], **options: object) -> list[str]:
    reply = ADAPTER.stream(_aiter(events), _meta(_map(_body(stream=True)), **options))
    assert reply.media_type == "text/event-stream"
    assert dict(reply.headers) == {"x-request-id": "r1", "x-trace-id": "t1"}
    out: list[str] = []
    async for frame in reply.frames:
        assert frame.startswith("data: ") and frame.endswith("\n\n"), frame
        out.append(frame[6:-2])
    return out


def _chunks(frames: list[str]) -> list[ChatCompletionChunk]:
    assert frames[-1] == "[DONE]"
    return [ChatCompletionChunk.model_validate(json.loads(f)) for f in frames[:-1]]


EVENTS: list[Event] = [
    Start(request_id="r1"),
    Metrics(input_tokens=2, output_tokens=1),
    Delta(text="hi "),
    ToolCall(call_id="c1", name="glossary_lookup", arguments={"term": "x"}),
    Delta(text="there"),
    Metrics(input_tokens=2, output_tokens=2, attempt=2),
    End(),
]


async def test_stream_is_role_chunk_deltas_finish_with_usage_then_done() -> None:
    chunks = _chunks(await _frames(EVENTS))
    assert all(c.id == "chatcmpl-r1" and c.object == "chat.completion.chunk" for c in chunks)
    assert all(c.model == "echo" and c.created == 1_700_000_000 for c in chunks)
    first, *deltas, last = chunks
    assert (first.choices[0].delta.role, first.choices[0].delta.content) == ("assistant", "")
    assert first.choices[0].finish_reason is None
    assert [c.choices[0].delta.content for c in deltas] == ["hi ", "there"]
    assert all(c.choices[0].delta.tool_calls is None for c in chunks), (
        "tool_call events stay inside"
    )
    assert last.choices[0].finish_reason == "stop" and last.choices[0].delta.content is None
    assert last.usage is not None
    assert (last.usage.prompt_tokens, last.usage.completion_tokens, last.usage.total_tokens) == (
        4,
        3,
        7,
    )
    assert all(c.usage is None for c in chunks[:-1])


async def test_stream_with_include_usage_ends_with_a_usage_only_chunk() -> None:
    chunks = _chunks(await _frames(EVENTS, include_usage=True))
    *_, finish, usage = chunks
    assert finish.choices[0].finish_reason == "stop" and finish.usage is None
    assert usage.choices == [] and usage.usage is not None and usage.usage.total_tokens == 7
    raw = json.loads((await _frames(EVENTS, include_usage=True))[0])
    assert "usage" in raw and raw["usage"] is None, "every other chunk carries usage: null"


async def test_stream_joins_to_the_complete_answer_for_json_output() -> None:
    events: list[Event] = [Start(request_id="r1"), End(output={"b": 1, "a": 2})]
    chunks = _chunks(await _frames(events))
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == '{"a":2,"b":1}'


async def test_stream_error_after_text_is_an_error_frame_then_done_without_finish() -> None:
    events: list[Event] = [
        Start(request_id="r1"),
        Delta(text="part"),
        Error(code="a2a.timeout", message="slow", retryable=True),
    ]
    frames = await _frames(events)
    assert frames[-1] == "[DONE]"
    error = json.loads(frames[-2])
    assert set(error) == {"error"}
    assert ErrorObject.model_validate(error["error"]).code == "a2a.timeout"
    assert error["error"]["type"] == "server_error" and error["error"]["retryable"] is True
    assert "part" not in error["error"]["message"], "streamed text is not repeated"
    chunks = [ChatCompletionChunk.model_validate(json.loads(f)) for f in frames[:-2]]
    assert all(c.choices[0].finish_reason is None for c in chunks)


async def test_stream_without_a_terminal_event_ends_with_an_error_frame() -> None:
    frames = await _frames([Start(request_id="r1"), Delta(text="x")])
    assert frames[-1] == "[DONE]" and json.loads(frames[-2])["error"]["code"] == "engine_error"


@pytest.mark.parametrize(
    ("code", "retryable", "status", "kind", "should_retry"),
    [
        ("invalid_body", False, 400, "invalid_request_error", "false"),
        ("model_not_found", False, 404, "invalid_request_error", "false"),
        ("not_ready", True, 503, "server_error", "true"),
        ("a2a.timeout", True, 504, "server_error", "true"),
        ("engine_error", False, 500, "server_error", "false"),
        ("a2a.transport", True, 503, "server_error", "true"),
        ("bad_output", False, 502, "server_error", "false"),
    ],
)
def test_error_status_type_and_should_retry_per_code(
    code: str, retryable: bool, status: int, kind: str, should_retry: str
) -> None:
    reply = ADAPTER.error(code, "m", retryable, ReplyMeta(IDS, SERVED, 0))
    assert reply.status == status
    assert reply.headers["x-should-retry"] == should_retry
    assert reply.headers["x-request-id"] == "r1" and reply.headers["x-trace-id"] == "t1"
    error = ErrorObject.model_validate(reply.body["error"])
    assert (error.type, error.code, error.message, error.param) == (kind, code, "m", None)
    assert reply.body["error"]["retryable"] is retryable


def test_a_refused_message_key_never_mentions_the_model_port() -> None:
    body = _body(messages=[{"role": "user", "content": "x", "audio": {"id": "a"}}])
    with pytest.raises(Refused) as info:
        _map(body)
    error = info.value.body["error"]
    assert error["code"] == "unsupported_message" and error["param"] == "messages[0].audio"
    assert "model port" not in error["message"]
    assert error["message"] == "audio is not supported by the OpenAI interface"
