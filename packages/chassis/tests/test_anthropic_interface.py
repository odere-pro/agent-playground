"""The Anthropic interface: `POST /v1/messages` on the public app, through the app with a fake
engine, and through the official `anthropic.AsyncAnthropic` client with only a base URL change
(PoC-3 open note, sections 2, 3, 6, and 11).

Exit criterion: "The OpenAI and Anthropic SDKs work with only a base URL change" (the Anthropic
half), with the hold rule, mid-stream errors, `x-should-retry`, and re-minting checked through the
SDK the way a client meets them. The SDK runs on `httpx2`, so it gets an `httpx2.ASGITransport`
client: in process, no socket, no key (a placeholder).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast, get_args, get_type_hints

import anthropic
import httpx
import httpx2
import pytest
from anthropic.types import ErrorResponse, Message, MessageCreateParams
from chassis.adapters.anthropic_compat.inbound import RAW_ONLY_PARAMS, READ_PARAMS
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.core.inbound import public_message
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.anthropic import _with_raw_only
from chassis.server.interfaces.errors import INTERFACE_KEY
from fastapi import FastAPI
from poc_harness.sse import sse_frames

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
PLACEHOLDER_KEY = "placeholder-not-a-key"


async def _say(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    """Scripted by the input text. `fail-first`: `engine_error` before any text; `retryable`: a
    retryable error before any text; `slow`: `a2a.timeout` before any text; `fail-late`: an error
    after one delta; `echo-data`: the input data as JSON text. Anything else: two deltas.
    """
    yield Start(request_id=ctx.request_id)
    yield Metrics(input_tokens=5, output_tokens=2)
    text = input.text or ""
    if text == "fail-first":
        yield Error(code="engine_error", message="boom")
        return
    if text == "retryable":
        yield Error(code="a2a.transport", message="gone", retryable=True)
        return
    if text == "slow":
        yield Error(code="a2a.timeout", message="slow", retryable=True)
        return
    if text == "echo-data":
        yield Delta(text=json.dumps(input.data, sort_keys=True))
        yield End()
        return
    yield Delta(text=f"you said {text}")
    if text == "fail-late":
        yield Error(code="a2a.timeout", message="late", retryable=True)
        return
    yield Metrics(input_tokens=1, output_tokens=3)
    yield Delta(text="!")
    yield End(status="ok")


def _app(engine: FakeEngine | None = None, config: dict[str, Any] | None = None) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=_say),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(ChassisConfig.model_validate(config or CONFIG), ports)


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _sdk(app: FastAPI, *, max_retries: int = 2) -> anthropic.AsyncAnthropic:
    """The official client; only the base URL differs from talking to Anthropic."""
    return anthropic.AsyncAnthropic(
        base_url="http://chassis",
        api_key=PLACEHOLDER_KEY,
        max_retries=max_retries,
        http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
    )


def _body(text: str = "hi", **extra: Any) -> dict[str, Any]:
    return {
        "model": "echo",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": text}],
        **extra,
    }


# --- through the official SDK ------------------------------------------------------------------


async def test_sdk_complete_with_only_a_base_url_change() -> None:
    """The official client gets a full Message, ids, usage, a request count, and one run span."""
    app = _app()
    async with _sdk(app) as client, app.router.lifespan_context(app):
        raw = await client.messages.with_raw_response.create(
            model="echo",
            max_tokens=64,
            system="be brief",
            messages=[{"role": "user", "content": "hello"}],
        )
        message = await raw.parse()
        assert len(app.state.runs) == 0
    assert isinstance(message, Message)
    assert [getattr(b, "text", None) for b in message.content] == ["you said hello!"]
    assert (message.stop_reason, message.model, message.role) == ("end_turn", "echo", "assistant")
    assert (message.usage.input_tokens, message.usage.output_tokens) == (6, 5)
    request_id = raw.headers["x-request-id"]
    assert message.id == f"msg_{request_id}"
    assert len(raw.headers["x-trace-id"]) == 32 and raw.headers["x-chassis-status"] == "ok"
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="anthropic") == 1
    runs = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert [s.attributes["interface"] for s in runs] == ["anthropic"] and runs[0].ended


async def test_sdk_messages_stream_gets_text_and_usage() -> None:
    """The SDK stream helper sees each text delta, the joined final message, and the usage."""
    app = _app()
    async with _sdk(app) as client, app.router.lifespan_context(app):
        async with client.messages.stream(
            model="echo", max_tokens=64, messages=[{"role": "user", "content": "hello"}]
        ) as stream:
            texts = [t async for t in stream.text_stream]
            final = await stream.get_final_message()
        assert len(app.state.runs) == 0
    assert texts == ["you said hello", "!"]
    assert [getattr(b, "text", None) for b in final.content] == ["you said hello!"]
    assert final.stop_reason == "end_turn"
    assert (final.usage.input_tokens, final.usage.output_tokens) == (6, 5), (
        "usage is visible to the SDK on a stream (message_delta)"
    )


async def test_sdk_create_stream_true_gives_the_raw_event_types() -> None:
    """A raw SDK stream yields Anthropic's seven event types in their official order."""
    app = _app()
    async with _sdk(app) as client, app.router.lifespan_context(app):
        stream = await client.messages.create(
            model="echo",
            max_tokens=64,
            stream=True,
            messages=[{"role": "user", "content": "hello"}],
        )
        kinds = [event.type async for event in stream]
    assert kinds == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("text", "status", "kind"),
    [("fail-first", 500, "api_error"), ("slow", 504, "timeout_error")],
)
async def test_sdk_an_error_before_text_is_an_http_error_and_not_retried(
    stream: bool, text: str, status: int, kind: str
) -> None:
    """The hold rule: nothing is sent before the first delta, so the error is HTTP. A
    non-retryable code says `x-should-retry: false`, so the SDK does not retry; a timeout says
    true, so the SDK retries it.
    """
    engine = FakeEngine(handle=_say)
    app = _app(engine)
    async with _sdk(app, max_retries=1) as client, app.router.lifespan_context(app):
        with pytest.raises(anthropic.InternalServerError) as info:
            await client.messages.create(
                model="echo",
                max_tokens=64,
                stream=stream,
                messages=[{"role": "user", "content": text}],
            )
        assert len(app.state.runs) == 0
    error = info.value
    assert error.status_code == status
    assert ErrorResponse.model_validate(error.body).error.type == kind
    should_retry = error.response.headers["x-should-retry"]
    assert should_retry == ("true" if status == 504 else "false")
    assert engine.runs == (2 if should_retry == "true" else 1)


async def test_sdk_a_retryable_error_is_retried_as_x_should_retry_says() -> None:
    """A retryable failure is a 503 overloaded error that says retry, so the SDK calls once more."""
    engine = FakeEngine(handle=_say)
    app = _app(engine)
    async with _sdk(app, max_retries=1) as client, app.router.lifespan_context(app):
        with pytest.raises(anthropic.InternalServerError) as info:
            await client.messages.create(
                model="echo", max_tokens=64, messages=[{"role": "user", "content": "retryable"}]
            )
    assert info.value.status_code == 503
    assert info.value.response.headers["x-should-retry"] == "true"
    assert ErrorResponse.model_validate(info.value.body).error.type == "overloaded_error"
    assert engine.runs == 2


async def test_sdk_an_error_mid_stream_is_raised_by_the_sdk() -> None:
    """An error after text reaches the SDK as an exception with the public message."""
    app = _app()
    texts: list[str] = []
    async with _sdk(app) as client, app.router.lifespan_context(app):
        with pytest.raises(anthropic.APIStatusError) as info:
            async with client.messages.stream(
                model="echo", max_tokens=64, messages=[{"role": "user", "content": "fail-late"}]
            ) as stream:
                async for text in stream.text_stream:
                    texts.append(text)  # noqa: PERF401 keeps the text before the error
        assert len(app.state.runs) == 0
    assert texts == ["you said fail-late"]
    body = ErrorResponse.model_validate(info.value.body)
    assert body.error.type == "timeout_error" and body.error.message == (
        f"a2a.timeout: {public_message('a2a.timeout')}"
    )


async def test_sdk_wrong_model_is_not_found_and_the_engine_never_runs() -> None:
    """A model that is not this agent raises NotFoundError, says do not retry, and runs nothing."""
    engine = FakeEngine(handle=_say)
    app = _app(engine)
    async with _sdk(app) as client, app.router.lifespan_context(app):
        with pytest.raises(anthropic.NotFoundError) as info:
            await client.messages.create(
                model="not-this-agent",
                max_tokens=64,
                messages=[{"role": "user", "content": "x"}],
            )
    body = ErrorResponse.model_validate(info.value.body)
    assert body.error.type == "not_found_error" and "model_not_found" in body.error.message
    assert info.value.response.headers["x-should-retry"] == "false"
    assert engine.runs == 0


async def test_sdk_request_id_is_the_chassis_request_id() -> None:
    """The Anthropic SDK reads `request-id` (not `x-request-id`) for `_request_id` and for an
    error's `request_id`; both are the run's request id, on a complete, a stream, and an error.
    """
    app = _app()
    user: list[Any] = [{"role": "user", "content": "hello"}]
    async with _sdk(app) as client, app.router.lifespan_context(app):
        message = await client.messages.create(model="echo", max_tokens=64, messages=user)
        raw = await client.messages.with_raw_response.create(
            model="echo", max_tokens=64, messages=user, stream=True
        )
        stream = await raw.parse()
        events = [event async for event in stream]
        with pytest.raises(anthropic.NotFoundError) as info:
            await client.messages.create(model="other", max_tokens=64, messages=user)
    assert message._request_id == message.id.removeprefix("msg_")
    start = events[0]
    assert start.type == "message_start"
    assert raw.request_id == start.message.id.removeprefix("msg_")
    assert raw.request_id == raw.headers["x-request-id"]
    assert info.value.request_id is not None
    assert info.value.request_id == ErrorResponse.model_validate(info.value.body).request_id


async def test_sdk_refused_parameters_are_bad_request() -> None:
    """Client tools sent through the SDK are refused as a bad request and the engine never runs."""
    engine = FakeEngine(handle=_say)
    app = _app(engine)

    async with _sdk(app) as client, app.router.lifespan_context(app):
        with pytest.raises(anthropic.BadRequestError) as tools:
            await client.messages.create(
                model="echo",
                max_tokens=64,
                messages=[{"role": "user", "content": "x"}],
                tools=[{"name": "f", "input_schema": {"type": "object"}}],
            )

    assert "tools" in ErrorResponse.model_validate(tools.value.body).error.message
    assert engine.runs == 0


async def test_sdk_mcp_servers_is_bad_request_when_sent_as_extra_body() -> None:
    """A key the SDK type drops, sent as extra body, is still read and refused before the engine."""
    engine = FakeEngine(handle=_say)
    app = _app(engine)

    async with _sdk(app) as client, app.router.lifespan_context(app):
        with pytest.raises(anthropic.BadRequestError) as mcp:
            await client.messages.create(
                model="echo",
                max_tokens=64,
                messages=[{"role": "user", "content": "x"}],
                extra_body={"mcp_servers": [{"type": "url", "url": "u", "name": "n"}]},
            )

    assert "mcp_servers" in ErrorResponse.model_validate(mcp.value.body).error.message
    assert engine.runs == 0


async def _ignored_call(app: FastAPI) -> Message:
    """One SDK call that sets ignored params, a dropped `metadata`, and the auth headers."""
    async with _sdk(app) as client, app.router.lifespan_context(app):
        return await client.messages.create(
            model="echo",
            max_tokens=64,
            messages=[{"role": "user", "content": "x"}],
            stop_sequences=["END"],
            metadata={"user_id": "person-1"},
            extra_body={"temperature": 0.2, "top_k": 4},
            extra_headers={"anthropic-beta": "some-beta"},
        )


def _ignored(telemetry: InMemoryTelemetry, param: str) -> int:
    return telemetry.counter_value("chassis.inbound_ignored", interface="anthropic", param=param)


async def test_sdk_ignored_parameters_are_counted_and_headers_never_logged() -> None:
    """Tuning params are counted once each, and no key, header value, or user id reaches a log."""
    app = _app()

    message = await _ignored_call(app)

    assert [getattr(b, "text", None) for b in message.content] == ["you said x!"]
    telemetry = _telemetry(app)
    counted = {p: _ignored(telemetry, p) for p in ("stop_sequences", "temperature", "top_k")}
    assert counted == {"stop_sequences": 1, "temperature": 1, "top_k": 1}
    dump = json.dumps([telemetry.logs, [s.attributes for s in telemetry.spans]], default=str)
    secrets = (PLACEHOLDER_KEY, "person-1", "some-beta", "2023-06-01")
    assert [secret for secret in secrets if secret in dump] == []


async def test_sdk_metadata_is_not_counted_when_it_is_sent() -> None:
    """`metadata` may name a person, so it is dropped without being counted as ignored."""
    app = _app()

    await _ignored_call(app)

    assert _ignored(_telemetry(app), "metadata") == 0


Answer = tuple[str, str]
"""The text the engine saw as its trace id, and the `x-trace-id` header."""


@dataclass(frozen=True)
class Overlap:
    """What three SDK calls with one traceparent saw."""

    first: Answer
    complete: Answer
    stream: Answer
    reminted: int


async def _overlapping_calls() -> Overlap:
    """Three SDK calls with one traceparent: the first holds it while a complete and then a
    stream run."""
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def wait(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        if input.text == "first":
            in_flight.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        yield Delta(text=ctx.trace_id)
        yield End()

    app = _app(FakeEngine(handle=wait))
    headers = {"traceparent": TRACEPARENT}
    async with _sdk(app, max_retries=0) as client, app.router.lifespan_context(app):

        async def call(text: str, stream: bool) -> Answer:
            if stream:
                async with client.messages.stream(
                    model="echo",
                    max_tokens=64,
                    messages=[{"role": "user", "content": text}],
                    extra_headers=headers,
                ) as s:
                    final: Message = await s.get_final_message()
                    trace = s.response.headers["x-trace-id"]
            else:
                raw = await client.messages.with_raw_response.create(
                    model="echo",
                    max_tokens=64,
                    messages=[{"role": "user", "content": text}],
                    extra_headers=headers,
                )
                final, trace = await raw.parse(), raw.headers["x-trace-id"]
            return getattr(final.content[0], "text", ""), trace

        first = asyncio.create_task(call("first", False))
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        second = await call("second", False)
        third = await call("third", True)
        release.set()
        done = await first
        assert len(app.state.runs) == 0
    reminted = _telemetry(app).counter_value("chassis.trace_id_reminted", interface="anthropic")
    return Overlap(first=done, complete=second, stream=third, reminted=reminted)


async def test_sdk_traceparent_is_the_trace_id_when_no_run_holds_it() -> None:
    """The first call keeps the caller's trace id, in its header and in the engine's context."""
    overlap = await _overlapping_calls()

    assert overlap.first == (TRACE, TRACE)


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_sdk_trace_id_is_reminted_when_a_run_in_flight_holds_it(lane: str) -> None:
    """A call reusing a busy trace id gets a fresh one, not a conflict, complete or streamed."""
    overlap = await _overlapping_calls()

    answer, trace = getattr(overlap, lane)
    assert answer == trace != TRACE


async def test_sdk_reminted_trace_ids_differ_when_two_calls_overlap_one_run() -> None:
    """Two calls that both meet the busy trace id each get their own new id."""
    overlap = await _overlapping_calls()

    assert overlap.complete[1] != overlap.stream[1]


async def test_sdk_reminting_is_counted_once_per_call_when_the_trace_id_is_busy() -> None:
    """The reminted counter goes up once for each of the two calls that met a busy trace id."""
    overlap = await _overlapping_calls()

    assert overlap.reminted == 2


# --- through the app with plain HTTP -----------------------------------------------------------


async def test_history_and_system_reach_the_engine_as_data() -> None:
    """System blocks and earlier turns posted over HTTP arrive in the engine's input data."""
    app = _app()
    body = _body(
        system=[{"type": "text", "text": "s1"}, {"type": "text", "text": "s2"}],
        messages=[
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": [{"type": "text", "text": "echo-data"}]},
        ],
    )
    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body)
    assert res.status_code == 200, res.text
    data = json.loads(res.json()["content"][0]["text"])
    assert data == {
        "history": [{"role": "user", "text": "q1"}, {"role": "assistant", "text": "a1"}],
        "system": "s1\ns2",
    }


REFUSED: list[tuple[str, dict[str, Any]]] = [
    ("tools", _body(tools=[{"name": "f", "input_schema": {"type": "object"}}])),
    ("tool_choice", _body(tool_choice={"type": "any"})),
    ("mcp_servers", _body(mcp_servers=[{"type": "url", "url": "u", "name": "n"}])),
    ("container", _body(container="c-1")),
    (
        "output_config.format",
        _body(output_config={"format": {"type": "json_schema", "schema": {}}}),
    ),
    (
        "image",
        _body(
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "image", "source": {"type": "url", "url": "https://x"}}],
                }
            ]
        ),
    ),
    (
        "tool_use",
        _body(
            messages=[
                {"role": "user", "content": "q"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t", "name": "f", "input": {}}],
                },
                {"role": "user", "content": "q2"},
            ]
        ),
    ),
    (
        "tool_result",
        _body(
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t", "content": "r"}],
                }
            ]
        ),
    ),
    (
        "thinking",
        _body(
            messages=[
                {"role": "user", "content": "q"},
                {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "t", "signature": "s"}],
                },
                {"role": "user", "content": "q2"},
            ]
        ),
    ),
    (
        "prefill",
        _body(messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "A"}]),
    ),
]


@pytest.mark.parametrize("body", [b for _, b in REFUSED], ids=[n for n, _ in REFUSED])
async def test_each_refused_class_is_400_and_the_engine_never_runs(body: dict[str, Any]) -> None:
    """Each refused body class is 400 with a request id, says do not retry, and runs nothing."""
    engine = FakeEngine(handle=_say)
    app = _app(engine)
    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body)
    assert res.status_code == 400, res.text
    error = ErrorResponse.model_validate(res.json())
    assert error.error.type == "invalid_request_error" and error.request_id
    assert res.headers["x-should-retry"] == "false"
    assert engine.runs == 0
    assert len(app.state.runs) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"model": "echo", "messages": [{"role": "user", "content": "x"}]},
        {**_body(), "stream": "maybe"},
        _body(messages=[{"role": "robot", "content": "x"}]),
        _body(messages=[{"role": "user", "content": [{"type": "no-such-block"}]}]),
        _body(system=[{"type": "text"}]),
    ],
    ids=["no max_tokens", "bad stream", "bad role (lazy)", "bad block (lazy)", "bad system"],
)
async def test_an_invalid_body_is_400_in_the_anthropic_shape_never_422_or_500(
    body: dict[str, Any],
) -> None:
    """A body that fails validation, even lazily, is 400 `invalid_body` in Anthropic's shape."""
    app = _app()
    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body)
    assert res.status_code == 400, res.text
    error = ErrorResponse.model_validate(res.json())
    assert error.error.type == "invalid_request_error"
    assert error.error.message.startswith("invalid_body: ")
    assert res.headers["x-should-retry"] == "false"


async def test_not_ready_is_503_overloaded_and_retryable() -> None:
    """Before startup the route answers 503 `overloaded_error` and tells the client to retry."""
    app = _app()
    async with _http(app) as client:
        res = await client.post("/v1/messages", json=_body())
    assert res.status_code == 503
    assert ErrorResponse.model_validate(res.json()).error.type == "overloaded_error"
    assert res.headers["x-should-retry"] == "true"


async def test_stream_over_http_is_sse_with_ids_in_the_headers() -> None:
    """A streamed reply is server-sent events with the trace and request ids in the headers."""
    app = _app()
    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/messages", json=_body("x", stream=True), headers={"traceparent": TRACEPARENT}
        )
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["x-trace-id"] == TRACE and res.headers["x-request-id"]
    assert "x-chassis-status" not in res.headers, "the status is not known when headers go out"
    frames = sse_frames(res.text)
    assert frames[0][0] == "message_start" and frames[-1][0] == "message_stop"


async def test_mid_stream_error_over_http_has_no_message_stop() -> None:
    """An error after text ends the event stream with `error` and sends no `message_stop`."""
    app = _app()
    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=_body("fail-late", stream=True))
    names = [name for name, _ in sse_frames(res.text)]
    assert names == ["message_start", "content_block_start", "content_block_delta", "error"]


def test_interface_off_is_not_mounted() -> None:
    """Turning the Anthropic interface off in the config removes `/v1/messages` from the app."""
    off = {**CONFIG, "spec": {**CONFIG["spec"], "interfaces": {"anthropic": False}}}
    assert "/v1/messages" not in _app(config=off).openapi()["paths"]


def test_openapi_declares_the_messages_operation() -> None:
    """The spec declares the SDK body, both reply types, error shapes, and no auth headers."""
    spec = _app().openapi()
    op = spec["paths"]["/v1/messages"]["post"]
    assert op["operationId"] == "messages" and op[INTERFACE_KEY] == "anthropic"
    schemas = spec["components"]["schemas"]
    body = json.dumps(op["requestBody"]["content"]["application/json"]["schema"])
    assert "MessageCreateParams" in body
    ok = op["responses"]["200"]["content"]
    assert ok["application/json"]["schema"]["$ref"].endswith("/Message")
    assert "text/event-stream" in ok
    for status in ("400", "404", "500", "502", "503", "504"):
        ref = op["responses"][status]["content"]["application/json"]["schema"]["$ref"]
        name = ref.rsplit("/", 1)[-1]
        assert name == "ErrorResponse" and {"type", "error"} <= set(schemas[name]["properties"])
    for header in ("anthropic-version", "x-api-key", "anthropic-beta"):
        assert header not in json.dumps(op.get("parameters", [])), "headers are not parameters"


async def test_messages_route_drops_an_unknown_key_when_it_is_deeply_nested() -> None:
    """An unread key is never walked, so no depth of nesting can raise `RecursionError`."""
    nested: Any = "x"
    for _ in range(2000):
        nested = {"a": nested}
    app = _app()

    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/messages",
            content=json.dumps(_body(unknown=nested)),
            headers={"content-type": "application/json"},
        )

    assert res.status_code == 200, res.text
    assert Message.model_validate(res.json()).content[0].type == "text"


def test_raw_only_params_are_the_read_keys_when_the_sdk_type_drops_them() -> None:
    """The router merges back exactly the keys the adapter reads and `MessageCreateParams` drops."""
    listed = set().union(*(get_type_hints(member) for member in get_args(MessageCreateParams)))

    assert READ_PARAMS - listed == RAW_ONLY_PARAMS
    assert sorted(RAW_ONLY_PARAMS) == ["mcp_servers", "temperature", "top_k", "top_p"]


# --- keys the SDK type drops (`RAW_ONLY_PARAMS`) -------------------------------------------------

REFUSED_RAW_ONLY: dict[str, Any] = {"mcp_servers": [{"type": "url", "url": "u", "name": "n"}]}
READ_RAW_ONLY: dict[str, Any] = {"temperature": 0.2, "top_p": 0.9, "top_k": 4}


def test_raw_only_cases_cover_every_dropped_key_when_split_into_refused_and_read() -> None:
    """The route cases below name each key the SDK type drops exactly once."""
    cases = [*REFUSED_RAW_ONLY, *READ_RAW_ONLY]

    assert sorted(cases) == sorted(RAW_ONLY_PARAMS)


@pytest.mark.parametrize(("key", "value"), REFUSED_RAW_ONLY.items())
async def test_messages_route_refuses_a_dropped_key_when_the_adapter_refuses_it(
    key: str, value: Any
) -> None:
    """A refused key the SDK type drops is still 400 on the public port; nothing runs."""
    engine = FakeEngine(handle=_say)
    app = _app(engine)

    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=_body(**{key: value}))

    assert res.status_code == 400, res.text
    error = ErrorResponse.model_validate(res.json()).error
    assert error.type == "invalid_request_error"
    assert error.message.startswith("unsupported_parameter: ") and key in error.message
    assert engine.runs == 0


@pytest.mark.parametrize(("key", "value"), READ_RAW_ONLY.items())
async def test_messages_route_counts_a_dropped_key_as_ignored_when_the_adapter_reads_it(
    key: str, value: Any
) -> None:
    """A tuning key the SDK type drops still reaches the adapter, which counts it as ignored."""
    app = _app()

    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=_body(**{key: value}))

    assert res.status_code == 200, res.text
    assert _ignored(_telemetry(app), key) == 1


def test_raw_merge_keeps_the_parsed_value_when_raw_json_sets_the_same_key() -> None:
    """On a conflict the parsed body wins; the route cannot reach this, parsing drops the keys."""
    parsed = cast(MessageCreateParams, {**_body(), "temperature": 0.1})

    merged = _with_raw_only(parsed, {**_body(), "temperature": 0.9})

    assert merged["temperature"] == 0.1


def test_raw_merge_takes_no_raw_key_when_the_key_is_not_raw_only() -> None:
    """Only the dropped keys the adapter reads come back from the raw JSON, nothing else."""
    parsed = cast(MessageCreateParams, _body())

    merged = _with_raw_only(parsed, {**_body(), "top_k": 4, "unknown": 1})

    assert merged == {**_body(), "top_k": 4}


@pytest.mark.parametrize("raw", [[1, 2], "text", 3, None], ids=["list", "str", "int", "null"])
def test_raw_merge_returns_the_parsed_body_when_the_raw_json_is_not_an_object(raw: Any) -> None:
    """A raw body that is not an object adds nothing, and the merge does not raise."""
    parsed = cast(MessageCreateParams, _body())

    merged = _with_raw_only(parsed, raw)

    assert merged == _body()


@pytest.mark.parametrize(
    "raw", [b"[1, 2]", b'"text"', b"3", b"null"], ids=["list", "str", "int", "null"]
)
async def test_messages_route_is_400_when_the_json_body_is_not_an_object(raw: bytes) -> None:
    """A JSON body that is not an object fails parsing with 400, so the merge never sees it."""
    engine = FakeEngine(handle=_say)
    app = _app(engine)

    async with _http(app) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/messages", content=raw, headers={"content-type": "application/json"}
        )

    assert res.status_code == 400, res.text
    assert ErrorResponse.model_validate(res.json()).error.type == "invalid_request_error"
    assert engine.runs == 0
