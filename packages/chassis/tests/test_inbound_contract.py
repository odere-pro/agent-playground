"""`InboundAdapterContract` bound three times: native, OpenAI, and Anthropic (PoC-3 open note,
section 9). The suite owns the logical cases and the canonical request each must give; a binding
gives only the format: how a logical request is written in it, and how a client of it reads an
answer back. The OpenAI and Anthropic readers use the official SDK types.

The OpenAI and Anthropic bindings skip, with the reason, until their adapter modules import.
"""

from __future__ import annotations

import functools
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

import pytest
from chassis.core.envelope import Budget, Response, TaskInput
from chassis.core.events import Error, parse_event
from chassis.core.inbound import InboundAdapter
from chassis.server.interfaces.native import NativeInbound, RunRequest
from chassis_contracts.inbound import (
    AGENT,
    BASE,
    BodyCase,
    Encode,
    InboundAdapterContract,
    Logical,
    Readback,
    ReadComplete,
    ReadError,
    ReadStream,
    Usage,
    sse,
)
from pydantic import TypeAdapter

NOT_A_KEY = "not-a-real-key"
"""Auth headers are accepted and ignored; the value is a placeholder, never a key."""


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _should_retry(headers: Mapping[str, str]) -> bool | None:
    value = _lower(headers).get("x-should-retry")
    if value is None:
        return None
    assert value in ("true", "false"), f"x-should-retry: {value!r}"
    return value == "true"


@functools.cache
def _type_adapter(tp: Any) -> TypeAdapter[Any]:
    """One `TypeAdapter` per type, kept alive: a lazy `ValidatorIterator` it returns refers to its
    validator, and pydantic-core panics when it is read after the adapter is collected (FastAPI
    keeps its own alive the same way).
    """
    return TypeAdapter(tp)


def _adapter_class(module: str, name: str) -> Callable[[], Any]:
    """The adapter class, or a clean skip while the module has not landed."""
    try:
        found = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name is not None and module.startswith(exc.name):
            pytest.skip(f"{module} has not landed yet")
        raise
    cls: Callable[[], Any] = getattr(found, name)
    return cls


# --- Native: the envelope as is ---


def native_encode(logical: Logical) -> tuple[RunRequest, Mapping[str, str]]:
    data: dict[str, Any] = {}
    if logical.system is not None:
        data["system"] = logical.system
    if logical.history:
        data["history"] = [{"role": r, "text": t} for r, t in logical.history]
    body = RunRequest(
        input=TaskInput(text=logical.text, data=data),
        budget=Budget(max_tokens=logical.max_tokens),
        stream=logical.stream,
    )
    return body, {}


def _native_http_error(status: int, headers: Mapping[str, str], body: dict[str, Any]) -> Readback:
    detail = body["detail"]
    if isinstance(detail, dict):
        error = ReadError(message=detail["message"], code=detail["code"])
    else:
        assert isinstance(detail, str)
        error = ReadError(message=detail, should_retry=_should_retry(headers))
    return Readback(text=None, usage=None, finish=None, status=status, error=error)


def _native_envelope(
    response: Response, status: int, *, text: str | None, error: ReadError | None
) -> Readback:
    metrics = response.metrics
    return Readback(
        text=text,
        usage=Usage(metrics["input_tokens"], metrics["output_tokens"]),
        finish=None,
        status=status,
        error=error,
        run_status=response.status,
        request_id=response.request_id,
    )


def native_read_complete(status: int, headers: Mapping[str, str], body: dict[str, Any]) -> Readback:
    if status != 200:
        return _native_http_error(status, headers, body)
    response = Response.model_validate(body)
    text = response.output.get("text")
    error = None
    if response.status == "error":
        failed = response.output["error"]
        error = ReadError(message=failed["message"], code=failed["code"])
    return _native_envelope(
        response, status, text=text if isinstance(text, str) else None, error=error
    )


def native_read_stream(status: int, headers: Mapping[str, str], frames: Sequence[str]) -> Readback:
    """One frame per event, then one `response` frame with the envelope, last."""
    events = sse(frames)
    assert events and events[-1].event == "response", "the stream ends with the response frame"
    text: list[str] = []
    error: ReadError | None = None
    for frame in events[:-1]:
        event = parse_event(json.loads(frame.data))
        assert frame.event == event.type, f"frame name {frame.event!r} != {event.type!r}"
        if event.type == "delta":
            assert error is None, "a delta after the error"
            text.append(event.text)
        elif isinstance(event, Error):
            error = ReadError(message=event.message, code=event.code)
    response = Response.model_validate_json(events[-1].data)
    return _native_envelope(response, status, text="".join(text), error=error)


class TestNativeInbound(InboundAdapterContract):
    """`POST /v1/run`: the native body is the canonical request with the ids optional."""

    model_field: ClassVar[str | None] = None
    errors_as_http: ClassVar[bool] = False
    finish_reason: ClassVar[str | None] = None
    output_as_text: ClassVar[bool] = False
    error_code_readable: ClassVar[bool] = False
    should_retry_header: ClassVar[bool] = False

    @pytest.fixture
    def adapter(self) -> InboundAdapter[Any]:
        return NativeInbound()

    @pytest.fixture
    def encode(self) -> Encode:
        return native_encode

    @pytest.fixture
    def read_complete(self) -> ReadComplete:
        return native_read_complete

    @pytest.fixture
    def read_stream(self) -> ReadStream:
        return native_read_stream

    @pytest.fixture
    def refused_cases(self) -> Sequence[BodyCase]:
        body, _ = native_encode(BASE)
        return [
            BodyCase("another agent", body.model_copy(update={"agent": "other"})),
            BodyCase("another version", body.model_copy(update={"agent_version": "9.9.9"})),
        ]

    @pytest.fixture
    def ignored_cases(self) -> Sequence[BodyCase]:
        body, _ = native_encode(BASE)
        served = body.model_copy(update={"agent": AGENT, "agent_version": "0.0.1"})
        with_ids = body.model_copy(
            update={"request_id": "body-req", "trace_id": "body-trace", "idempotency_key": "k"}
        )
        return [
            BodyCase("auth headers", body, {"authorization": f"Bearer {NOT_A_KEY}"}),
            BodyCase("x-api-key", body, {"x-api-key": NOT_A_KEY}),
            BodyCase("SDK version headers", body, {"anthropic-version": "2023-06-01"}),
            BodyCase("the served agent named", served),
            # The router resolves the ids (section 6); the adapter copies the ones it is given.
            BodyCase("body ids", with_ids),
        ]


# --- OpenAI: chat completions, read with the `openai` SDK types ---


def _openai_validate(body: dict[str, Any]) -> Any:
    """What the router hands `to_request`: the body validated as `CompletionCreateParams`, with
    `messages` (and list contents) as lazy iterators."""
    from openai.types.chat import CompletionCreateParams

    validated: dict[str, Any] = _type_adapter(CompletionCreateParams).validate_python(body)
    dropped = set(body) - set(validated)
    assert not dropped, f"the SDK type drops {dropped}: the case does not reach the adapter"
    return validated


def _openai_body(logical: Logical) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if logical.system is not None:
        messages.append({"role": "system", "content": logical.system})
    messages.extend({"role": role, "content": text} for role, text in logical.history)
    messages.append({"role": "user", "content": logical.text})
    return {
        "model": AGENT,
        "messages": messages,
        "max_completion_tokens": logical.max_tokens,
        "stream": logical.stream,
    }


def openai_encode(logical: Logical) -> tuple[Any, Mapping[str, str]]:
    return _openai_validate(_openai_body(logical)), {}


def _openai_error(
    status: int, headers: Mapping[str, str] | None, payload: dict[str, Any]
) -> ReadError:
    from openai.types.shared import ErrorObject

    error = ErrorObject.model_validate(payload["error"])
    return ReadError(
        message=error.message,
        code=error.code,
        type=error.type,
        should_retry=None if headers is None else _should_retry(headers),
    )


def openai_read_complete(status: int, headers: Mapping[str, str], body: dict[str, Any]) -> Readback:
    from openai.types.chat import ChatCompletion

    h = _lower(headers)
    if status != 200:
        return Readback(None, None, None, status, _openai_error(status, headers, body))
    completion = ChatCompletion.model_validate(body)
    assert completion.object == "chat.completion"
    assert completion.model == AGENT
    assert len(completion.choices) == 1, "one answer per run"
    choice = completion.choices[0]
    message = choice.message
    assert message.role == "assistant"
    tool_calls = len(message.tool_calls or []) + (message.function_call is not None)
    usage = None
    if completion.usage is not None:
        u = completion.usage
        assert u.total_tokens == u.prompt_tokens + u.completion_tokens
        usage = Usage(u.prompt_tokens, u.completion_tokens)
    assert completion.id.startswith("chatcmpl-"), completion.id
    request_id = completion.id.removeprefix("chatcmpl-")
    assert h.get("x-request-id") == request_id, "x-request-id is the id's request id"
    return Readback(
        text=message.content or "",
        usage=usage,
        finish=choice.finish_reason,
        status=status,
        error=None,
        run_status=h.get("x-chassis-status"),
        request_id=request_id,
        tool_calls=tool_calls,
    )


def openai_read_stream(status: int, headers: Mapping[str, str], frames: Sequence[str]) -> Readback:
    """`data:` chunks, the first one `delta {role: assistant}`, at most one error frame, then
    `data: [DONE]` last."""
    from openai.types.chat import ChatCompletionChunk

    data = [event.data for event in sse(frames)]
    assert data and data[-1] == "[DONE]", "the stream ends with data: [DONE]"
    assert "[DONE]" not in data[:-1], "one [DONE]"
    text: list[str] = []
    usage: Usage | None = None
    finish: str | None = None
    error: ReadError | None = None
    tool_calls = 0
    ids: set[str] = set()
    first = True
    for raw in data[:-1]:
        payload = json.loads(raw)
        assert error is None, "nothing but [DONE] after the error frame"
        if "error" in payload:
            error = _openai_error(status, None, payload)
            continue
        chunk = ChatCompletionChunk.model_validate(payload)
        assert chunk.object == "chat.completion.chunk"
        assert chunk.model == AGENT
        ids.add(chunk.id)
        if chunk.usage is not None:
            assert usage is None, "usage is sent once"
            u = chunk.usage
            assert u.total_tokens == u.prompt_tokens + u.completion_tokens
            usage = Usage(u.prompt_tokens, u.completion_tokens)
        for choice in chunk.choices:
            assert finish is None, "nothing after the finish chunk"
            if first:
                assert choice.delta.role == "assistant", "the first chunk names the role"
                first = False
            if choice.delta.content:
                text.append(choice.delta.content)
            tool_calls += len(choice.delta.tool_calls or [])
            tool_calls += choice.delta.function_call is not None
            if choice.finish_reason is not None:
                finish = choice.finish_reason
    assert len(ids) <= 1, f"one id per stream: {ids}"
    request_id = None
    if ids:
        (chunk_id,) = ids
        assert chunk_id.startswith("chatcmpl-"), chunk_id
        request_id = chunk_id.removeprefix("chatcmpl-")
    return Readback(
        text="".join(text),
        usage=usage,
        finish=finish,
        status=status,
        error=error,
        run_status=_lower(headers).get("x-chassis-status"),
        request_id=request_id,
        tool_calls=tool_calls,
    )


def _openai_with(**params: Any) -> Any:
    return _openai_validate({**_openai_body(BASE), **params})


def _openai_messages(*messages: dict[str, Any]) -> list[dict[str, Any]]:
    """BASE's messages with `messages` before the last user turn."""
    base: list[dict[str, Any]] = _openai_body(BASE)["messages"]
    return [*base[:-1], *messages, base[-1]]


class TestOpenAIInbound(InboundAdapterContract):
    """The OpenAI interface (`POST /v1/chat/completions` on the public port)."""

    finish_reason: ClassVar[str | None] = "stop"
    error_types: ClassVar[Mapping[int, str]] = {
        400: "invalid_request_error",
        404: "invalid_request_error",
        500: "server_error",
        502: "server_error",
        503: "server_error",
        504: "server_error",
    }

    @pytest.fixture
    def adapter(self) -> InboundAdapter[Any]:
        cls = _adapter_class("chassis.adapters.openai_compat.inbound", "OpenAIInbound")
        adapter: InboundAdapter[Any] = cls()
        return adapter

    @pytest.fixture
    def encode(self) -> Encode:
        return openai_encode

    @pytest.fixture
    def read_complete(self) -> ReadComplete:
        return openai_read_complete

    @pytest.fixture
    def read_stream(self) -> ReadStream:
        return openai_read_stream

    @pytest.fixture
    def refused_cases(self) -> Sequence[BodyCase]:
        tool = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
        audio = {"type": "input_audio", "input_audio": {"data": "AA==", "format": "wav"}}
        last_user = {"role": "user", "content": [{"type": "text", "text": "Explain SLM."}, image]}
        base = _openai_body(BASE)["messages"]
        return [
            BodyCase("n > 1", _openai_with(n=2)),
            BodyCase("logprobs", _openai_with(logprobs=True)),
            BodyCase("top_logprobs", _openai_with(top_logprobs=2)),
            BodyCase("tools", _openai_with(tools=[tool])),
            BodyCase("functions", _openai_with(functions=[{"name": "f"}])),
            BodyCase("tool_choice required", _openai_with(tool_choice="required")),
            BodyCase("response_format json", _openai_with(response_format={"type": "json_object"})),
            BodyCase("audio", _openai_with(audio={"voice": "alloy", "format": "wav"})),
            BodyCase("modalities audio", _openai_with(modalities=["text", "audio"])),
            BodyCase("web_search_options", _openai_with(web_search_options={})),
            BodyCase("image part", _openai_with(messages=[*base[:-1], last_user])),
            BodyCase(
                "audio part",
                _openai_with(messages=_openai_messages({"role": "user", "content": [audio]})),
            ),
            BodyCase(
                "assistant tool_calls in history",
                _openai_with(
                    messages=_openai_messages(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "f", "arguments": "{}"},
                                }
                            ],
                        },
                        {"role": "tool", "tool_call_id": "c1", "content": "done"},
                    )
                ),
            ),
            BodyCase(
                "last message is not user",
                _openai_with(messages=[*base, {"role": "assistant", "content": "Prefill"}]),
            ),
            BodyCase("max_tokens differs", _openai_with(max_tokens=10)),
        ]

    @pytest.fixture
    def ignored_cases(self) -> Sequence[BodyCase]:
        body = _openai_body(BASE)
        _system, *rest = body["messages"]
        lines = BASE.system.split("\n") if BASE.system else []
        split_system = [
            {"role": "system", "content": lines[0]},
            {"role": "developer", "content": lines[1]},
            *rest,
        ]
        parts = [
            *body["messages"][:-1],
            {"role": "user", "content": [{"type": "text", "text": BASE.text}]},
        ]
        tokens = {k: v for k, v in body.items() if k != "max_completion_tokens"}
        return [
            BodyCase("temperature", _openai_with(temperature=0.2)),
            BodyCase("top_p", _openai_with(top_p=0.9)),
            BodyCase("seed", _openai_with(seed=7)),
            BodyCase("stop", _openai_with(stop=["\n\n"])),
            BodyCase("penalties", _openai_with(presence_penalty=0.1, frequency_penalty=0.1)),
            BodyCase("logit_bias", _openai_with(logit_bias={"50256": -100})),
            BodyCase("store", _openai_with(store=False)),
            BodyCase("service_tier", _openai_with(service_tier="auto")),
            BodyCase("reasoning_effort", _openai_with(reasoning_effort="low")),
            BodyCase("metadata, user", _openai_with(metadata={"k": "v"}, user="u-1")),
            BodyCase("safety_identifier", _openai_with(safety_identifier="s-1")),
            BodyCase("tool_choice none", _openai_with(tool_choice="none")),
            BodyCase("tool_choice auto", _openai_with(tool_choice="auto")),
            BodyCase("empty tools", _openai_with(tools=[])),
            BodyCase("stream_options", _openai_with(stream_options={"include_usage": True})),
            BodyCase("auth header", _openai_with(), {"authorization": f"Bearer {NOT_A_KEY}"}),
            BodyCase("max_tokens spelling", _openai_validate({**tokens, "max_tokens": 300})),
            BodyCase("both max token fields equal", _openai_with(max_tokens=300)),
            BodyCase("system and developer joined", _openai_with(messages=split_system)),
            BodyCase("text parts", _openai_with(messages=parts)),
        ]


# --- Anthropic: messages, read with the `anthropic` SDK types ---


def _anthropic_validate(body: dict[str, Any]) -> Any:
    """The body validated as `MessageCreateParams`, as the router hands it on. Keys the SDK type
    does not know are dropped there, so a case must use keys that survive."""
    from anthropic.types import MessageCreateParams

    validated: dict[str, Any] = _type_adapter(MessageCreateParams).validate_python(body)
    dropped = set(body) - set(validated)
    assert not dropped, f"the SDK type drops {dropped}: the case does not reach the adapter"
    return validated


def _anthropic_body(logical: Logical) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": AGENT,
        "max_tokens": logical.max_tokens,
        "messages": [
            *({"role": role, "content": text} for role, text in logical.history),
            {"role": "user", "content": logical.text},
        ],
        "stream": logical.stream,
    }
    if logical.system is not None:
        body["system"] = logical.system
    return body


def anthropic_encode(logical: Logical) -> tuple[Any, Mapping[str, str]]:
    return _anthropic_validate(_anthropic_body(logical)), {}


def _anthropic_error(headers: Mapping[str, str] | None, payload: dict[str, Any]) -> ReadError:
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
        should_retry=None if headers is None else _should_retry(headers),
    )


def anthropic_read_complete(
    status: int, headers: Mapping[str, str], body: dict[str, Any]
) -> Readback:
    from anthropic.types import Message

    h = _lower(headers)
    if status != 200:
        return Readback(None, None, None, status, _anthropic_error(headers, body))
    message = Message.model_validate(body)
    assert message.type == "message" and message.role == "assistant"
    assert message.model == AGENT
    assert message.id.startswith("msg_"), message.id
    request_id = message.id.removeprefix("msg_")
    assert h.get("x-request-id") == request_id, "x-request-id is the id's request id"
    text = "".join(block.text for block in message.content if block.type == "text")
    tool_calls = sum(block.type in ("tool_use", "server_tool_use") for block in message.content)
    return Readback(
        text=text,
        usage=Usage(message.usage.input_tokens, message.usage.output_tokens),
        finish=message.stop_reason,
        status=status,
        error=None,
        run_status=h.get("x-chassis-status"),
        request_id=request_id,
        tool_calls=tool_calls,
    )


def anthropic_read_stream(
    status: int, headers: Mapping[str, str], frames: Sequence[str]
) -> Readback:
    """`message_start` first; text blocks opened before their deltas and closed; then
    `message_delta` and `message_stop` last, or `event: error` last with no `message_stop`."""
    from anthropic.types import RawMessageStreamEvent

    parse = _type_adapter(RawMessageStreamEvent)
    events = sse(frames)
    assert events, "an empty stream"
    text: list[str] = []
    finish: str | None = None
    usage: Usage | None = None
    error: ReadError | None = None
    request_id: str | None = None
    input_tokens = 0
    tool_calls = 0
    open_blocks: set[int] = set()
    stopped = False
    for i, frame in enumerate(events):
        payload = json.loads(frame.data)
        assert frame.event == payload["type"], f"event {frame.event!r} != {payload['type']!r}"
        assert error is None and not stopped, f"a {frame.event} after the end of the stream"
        if payload["type"] == "error":
            error = _anthropic_error(None, payload)
            continue
        if payload["type"] == "ping":
            continue
        event = parse.validate_python(payload)
        assert (event.type == "message_start") == (i == 0), "message_start comes first, once"
        if event.type == "message_start":
            assert event.message.content == [] and event.message.stop_reason is None
            assert event.message.model == AGENT
            assert event.message.id.startswith("msg_"), event.message.id
            request_id = event.message.id.removeprefix("msg_")
            input_tokens = event.message.usage.input_tokens
        elif event.type == "content_block_start":
            open_blocks.add(event.index)
            if event.content_block.type != "text":
                tool_calls += event.content_block.type in ("tool_use", "server_tool_use")
            else:
                text.append(event.content_block.text)
        elif event.type == "content_block_delta":
            assert event.index in open_blocks, "a delta for a block that is not open"
            if event.delta.type == "text_delta":
                text.append(event.delta.text)
        elif event.type == "content_block_stop":
            open_blocks.remove(event.index)
        elif event.type == "message_delta":
            assert not open_blocks, "message_delta with a block still open"
            finish = event.delta.stop_reason
            given = event.usage.input_tokens
            usage = Usage(input_tokens if given is None else given, event.usage.output_tokens)
        elif event.type == "message_stop":
            stopped = True
    if error is None:
        assert stopped, "a stream without an error ends with message_stop"
    return Readback(
        text="".join(text),
        usage=usage,
        finish=finish,
        status=status,
        error=error,
        run_status=_lower(headers).get("x-chassis-status"),
        request_id=request_id,
        tool_calls=tool_calls,
    )


def _anthropic_with(**params: Any) -> Any:
    return _anthropic_validate({**_anthropic_body(BASE), **params})


def _anthropic_before_last(*messages: dict[str, Any]) -> Any:
    base: list[dict[str, Any]] = _anthropic_body(BASE)["messages"]
    return _anthropic_with(messages=[*base[:-1], *messages, base[-1]])


class TestAnthropicInbound(InboundAdapterContract):
    """The Anthropic interface (`POST /v1/messages` on the public port)."""

    finish_reason: ClassVar[str | None] = "end_turn"
    error_types: ClassVar[Mapping[int, str]] = {
        400: "invalid_request_error",
        404: "not_found_error",
        500: "api_error",
        502: "api_error",
        503: "overloaded_error",
        504: "timeout_error",
    }

    @pytest.fixture
    def adapter(self) -> InboundAdapter[Any]:
        cls = _adapter_class("chassis.adapters.anthropic_compat.inbound", "AnthropicInbound")
        adapter: InboundAdapter[Any] = cls()
        return adapter

    @pytest.fixture
    def encode(self) -> Encode:
        return anthropic_encode

    @pytest.fixture
    def read_complete(self) -> ReadComplete:
        return anthropic_read_complete

    @pytest.fixture
    def read_stream(self) -> ReadStream:
        return anthropic_read_stream

    @pytest.fixture
    def refused_cases(self) -> Sequence[BodyCase]:
        tool = {"name": "f", "input_schema": {"type": "object"}}
        image = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "AA=="},
        }
        document = {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": "doc"},
        }
        base = _anthropic_body(BASE)["messages"]
        return [
            BodyCase("tools", _anthropic_with(tools=[tool])),
            BodyCase("tool_choice any", _anthropic_with(tool_choice={"type": "any"})),
            BodyCase("container", _anthropic_with(container="c-1")),
            BodyCase(
                "output_config json",
                _anthropic_with(
                    output_config={"format": {"type": "json_schema", "schema": {"type": "object"}}}
                ),
            ),
            BodyCase(
                "image block",
                _anthropic_with(
                    messages=[
                        *base[:-1],
                        {"role": "user", "content": [{"type": "text", "text": BASE.text}, image]},
                    ]
                ),
            ),
            BodyCase(
                "document block",
                _anthropic_before_last(
                    {"role": "user", "content": [document]}, {"role": "assistant", "content": "ok"}
                ),
            ),
            BodyCase(
                "tool_use and tool_result in history",
                _anthropic_before_last(
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "t1", "name": "f", "input": {}}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}],
                    },
                    {"role": "assistant", "content": "ok"},
                ),
            ),
            BodyCase(
                "thinking block in history",
                _anthropic_before_last(
                    {"role": "user", "content": "think"},
                    {
                        "role": "assistant",
                        "content": [{"type": "thinking", "thinking": "hm", "signature": "s"}],
                    },
                ),
            ),
            BodyCase(
                "assistant prefill",
                _anthropic_with(messages=[*base, {"role": "assistant", "content": "Prefill"}]),
            ),
        ]

    @pytest.fixture
    def ignored_cases(self) -> Sequence[BodyCase]:
        lines = BASE.system.split("\n") if BASE.system else []
        blocks = [{"type": "text", "text": line} for line in lines]
        base = _anthropic_body(BASE)["messages"]
        text_block = [
            *base[:-1],
            {"role": "user", "content": [{"type": "text", "text": BASE.text}]},
        ]
        return [
            BodyCase("stop_sequences", _anthropic_with(stop_sequences=["\n\n"])),
            BodyCase(
                "thinking", _anthropic_with(thinking={"type": "enabled", "budget_tokens": 1024})
            ),
            BodyCase("metadata", _anthropic_with(metadata={"user_id": "u-1"})),
            BodyCase("service_tier", _anthropic_with(service_tier="auto")),
            BodyCase("cache_control", _anthropic_with(cache_control={"type": "ephemeral"})),
            BodyCase(
                "headers",
                _anthropic_with(),
                {
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "some-beta",
                    "x-api-key": NOT_A_KEY,
                },
            ),
            BodyCase("system as text blocks", _anthropic_with(system=blocks)),
            BodyCase("user text block", _anthropic_with(messages=text_block)),
        ]
