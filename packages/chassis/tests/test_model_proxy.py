"""The model pass-through: `POST /v1/chat/completions` on the proxy app maps the OpenAI shape to
`ModelPort` and back, streaming and complete, forwards no `Authorization` header, and counts calls
per route. A call whose `traceparent` names an in-flight run gets a span and is charged to that
run's budget; an exhausted run is refused; a call without one is served and counted uncorrelated.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest
from chassis.adapters.anthropic_compat.model_wire import BODY_CAP_BYTES
from chassis.adapters.litellm import LiteLLMModel
from chassis.fakes import InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    Usage,
)
from chassis.server import ChassisConfig, create_app
from chassis.server.correlation import RunRecord
from chassis.server.model_admission import DEFAULT_UNCORRELATED_MAX_TOKENS
from chassis.server.proxy_app import create_proxy_app
from chassis.server.remote_auth import create_remote_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    PLAIN_WORDS,
    TRACE,
    TRACEPARENT,
    FailingModel,
    GatedModel,
    RecordingModel,
    abandon_stream,
    asgi_client,
    proxy_ports,
    run_request,
)

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}

BODY: dict[str, Any] = {
    "model": "big-default",
    "messages": [
        {"role": "system", "content": "Rewrite in plain words."},
        {"role": "user", "content": "simplify: the quick brown fox"},
    ],
    "temperature": 0.2,
    "max_tokens": 64,
}


CHAT_RULES = [
    ScriptRule(match="lookup", tool_call=ToolCallRequest(call_id="c1", name="glossary_lookup")),
    ScriptRule(match="fail", error="upstream_down", retryable=True),
    ScriptRule(match="simplify", reply="Plain words. Short sentences."),
]
"""A tool call for `lookup`, a retryable failure for `fail`, and a plain reply."""


def _chat_ports() -> PortBundle:
    return proxy_ports(ScriptedModel(CHAT_RULES))


def _chunks(text: str) -> list[Any]:
    out: list[Any] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


async def test_complete_maps_the_openai_shape_both_ways() -> None:
    ports = _chat_ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=BODY)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["object"] == "chat.completion" and data["model"] == "big-default"
    choice = data["choices"][0]
    assert choice["message"] == {"role": "assistant", "content": "Plain words. Short sentences."}
    assert choice["finish_reason"] == "stop"
    assert data["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    model = ports.model
    assert isinstance(model, ScriptedModel)
    assert model.calls[-1] == [
        ModelMessage(role="system", content="Rewrite in plain words."),
        ModelMessage(role="user", content="simplify: the quick brown fox"),
    ]
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.model_calls", route="big-default") == 1


async def test_stream_ends_with_usage_and_done() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _chat_ports())
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json={**BODY, "stream": True})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    chunks = _chunks(res.text)
    assert chunks[-1] == "[DONE]"
    last = chunks[-2]
    assert last["object"] == "chat.completion.chunk"
    assert last["choices"][0]["finish_reason"] == "stop"
    assert last["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks[:-1])
    assert text == "Plain words. Short sentences."
    assert all(c["model"] == "big-default" for c in chunks[:-1])


async def test_tool_calls_cross_in_both_modes() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _chat_ports())
    body = {
        **BODY,
        "messages": [{"role": "user", "content": "lookup SLM"}],
        "tools": [{"type": "function", "function": {"name": "glossary_lookup", "parameters": {}}}],
    }
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        complete = (await client.post("/v1/chat/completions", json=body)).json()
        streamed = _chunks(
            (await client.post("/v1/chat/completions", json={**body, "stream": True})).text
        )
    call = complete["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "glossary_lookup" and call["id"] == "c1"
    assert complete["choices"][0]["finish_reason"] == "tool_calls"
    tool_chunks = [c for c in streamed[:-1] if c["choices"][0]["delta"].get("tool_calls")]
    assert tool_chunks and tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
    assert streamed[-2]["choices"][0]["finish_reason"] == "tool_calls"


async def test_model_error_is_an_openai_error_body() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _chat_ports())
    body = {**BODY, "messages": [{"role": "user", "content": "fail"}]}
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        complete = await client.post("/v1/chat/completions", json=body)
        streamed = await client.post("/v1/chat/completions", json={**body, "stream": True})
    assert complete.status_code == 502
    assert complete.json()["error"]["code"] == "upstream_down"
    chunks = _chunks(streamed.text)
    assert chunks[-1] == "[DONE]" and chunks[-2]["error"]["code"] == "upstream_down"
    assert chunks[-2]["error"]["retryable"] is True


async def test_inbound_authorization_is_never_forwarded() -> None:
    upstream: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        upstream.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(record))
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(model))
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": "Bearer leaked-key"}
        )
    assert res.status_code == 200
    assert upstream and "authorization" not in {k.lower() for k in upstream[0].headers}
    assert "leaked-key" not in res.text


async def test_not_ready_is_503() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _chat_ports())
    async with asgi_client(create_proxy_app(app)) as client:
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 503


async def test_a_call_of_an_in_flight_run_gets_a_span_and_is_charged() -> None:
    ports = _chat_ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    headers = {"traceparent": TRACEPARENT}
    request, ctx = run_request(max_tokens=100)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        complete = await client.post("/v1/chat/completions", json=BODY, headers=headers)
        streamed = await client.post(
            "/v1/chat/completions", json={**BODY, "stream": True}, headers=headers
        )
    assert complete.status_code == 200 and streamed.status_code == 200
    assert record.model_calls == 2
    assert (record.spent_input_tokens, record.spent_output_tokens) == (20, 10)
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    spans = [s for s in telemetry.spans if s.name == "chassis.model.call"]
    assert len(spans) == 2 and all(s.ended for s in spans)
    for span in spans:
        assert span.attributes["request_id"] == "req-1"
        assert span.attributes["trace_id"] == TRACE
        assert span.attributes["route"] == "big-default"
        assert span.attributes["correlated"] is True
    assert telemetry.counter_value("chassis.model_calls_uncorrelated", route="big-default") == 0


@pytest.mark.parametrize(
    "headers",
    [{}, {"traceparent": "garbage"}, {"traceparent": f"00-{'b' * 32}-00f067aa0ba902b7-01"}],
    ids=["missing", "malformed", "no-run"],
)
async def test_a_call_without_an_in_flight_run_is_served_and_counted(
    headers: dict[str, str],
) -> None:
    ports = _chat_ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = run_request(max_tokens=100)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert res.status_code == 200, res.text
    assert record.model_calls == 0, "an uncorrelated call is never charged to a run"
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.model_calls_uncorrelated", route="big-default") == 1
    assert telemetry.counter_value("chassis.model_calls", route="big-default") == 1
    warnings = [log for log in telemetry.logs if log["level"] == "warning"]
    assert len(warnings) == 1 and warnings[0]["route"] == "big-default"


async def test_an_exhausted_run_is_refused_with_429_complete_and_streamed() -> None:
    ports = _chat_ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    headers = {"traceparent": TRACEPARENT}
    request, ctx = run_request(max_tokens=15)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        first = await client.post("/v1/chat/completions", json=BODY, headers=headers)
        refused = await client.post("/v1/chat/completions", json=BODY, headers=headers)
        streamed = await client.post(
            "/v1/chat/completions", json={**BODY, "stream": True}, headers=headers
        )
    assert first.status_code == 200 and record.exhausted
    assert refused.status_code == 429
    error = refused.json()["error"]
    assert error["code"] == "budget_exhausted" and error["type"] == "budget_exhausted"
    assert error["retryable"] is False
    assert streamed.status_code == 429
    assert streamed.headers["content-type"].startswith("text/event-stream")
    chunks = _chunks(streamed.text)
    assert len(chunks) == 2 and chunks[-1] == "[DONE]"
    assert chunks[0]["error"]["code"] == "budget_exhausted"
    assert record.model_calls == 1, "a refused call never reaches the model"
    model = ports.model
    assert isinstance(model, ScriptedModel) and len(model.calls) == 1
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.model_calls_refused", route="big-default") == 2


async def test_a_failed_stream_is_charged_with_what_it_reported() -> None:
    ports = _chat_ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    body = {**BODY, "messages": [{"role": "user", "content": "fail"}], "stream": True}
    request, ctx = run_request(max_tokens=100)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await client.post(
            "/v1/chat/completions", json=body, headers={"traceparent": TRACEPARENT}
        )
    assert _chunks(res.text)[-2]["error"]["code"] == "upstream_down"
    assert record.model_calls == 1 and record.spent_tokens == 0


# --- The forwarded `max_tokens`: capped at what the run has left, reserved while the call runs ---


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize(
    ("asked", "forwarded"),
    [(None, 5), (1000, 5), (64, 5), (3, 3)],
    ids=["unset", "1000", "64", "under"],
)
async def test_the_forwarded_max_tokens_is_capped_at_the_remainder(
    stream: bool, asked: int | None, forwarded: int
) -> None:
    """A run with `max_tokens: 20` that has spent 15 forwards at most 5; unset is the remainder."""
    model = RecordingModel(PLAIN_WORDS)
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(model))
    body: dict[str, Any] = {**BODY, "stream": stream}
    body.pop("max_tokens")
    if asked is not None:
        body["max_tokens"] = asked
    request, ctx = run_request(max_tokens=20)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        record.charge(Usage(input_tokens=10, output_tokens=5))
        res = await client.post(
            "/v1/chat/completions", json=body, headers={"traceparent": TRACEPARENT}
        )
        assert res.status_code == 200, res.text
        assert record.reserved_tokens == 0, "the reservation is settled when the call ends"
    assert model.max_tokens == [forwarded]
    assert len(model.calls) == 1
    assert record.spent_tokens == 30 and record.model_calls == 2


@pytest.mark.parametrize("asked", [None, 1000])
async def test_an_uncorrelated_call_forwards_its_max_tokens_or_the_default_ceiling(
    asked: int | None,
) -> None:
    """PoC-5: an uncorrelated call with no `max_tokens` is forwarded with the default ceiling,
    so the per-replica cap can reserve its worst case."""
    model = RecordingModel(PLAIN_WORDS)
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(model))
    body = {**BODY, "max_tokens": asked}
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 200
    assert model.max_tokens == [DEFAULT_UNCORRELATED_MAX_TOKENS if asked is None else asked]


async def test_a_concurrent_call_cannot_be_given_tokens_another_call_holds() -> None:
    """Call A streams with no `max_tokens`, so it holds the run's whole remainder until its
    usage is known. Call B of the same run, sent meanwhile, is refused with 429 and
    `retryable: true` (the tokens are held, not spent). Once A settles, B's retry gets what A
    left.
    """
    gate = asyncio.Event()
    model = RecordingModel(PLAIN_WORDS, gate=gate)
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(model))
    headers = {"traceparent": TRACEPARENT}
    unset = {k: v for k, v in BODY.items() if k != "max_tokens"}
    request, ctx = run_request(max_tokens=40)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        first = asyncio.create_task(
            client.post("/v1/chat/completions", json={**unset, "stream": True}, headers=headers)
        )
        await asyncio.wait_for(model.streaming.wait(), timeout=5)
        assert record.reserved_tokens == 40 and record.remaining_tokens == 0
        held = await client.post("/v1/chat/completions", json=BODY, headers=headers)
        gate.set()
        streamed = await asyncio.wait_for(first, timeout=5)
        retry = await client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert streamed.status_code == 200
    assert held.status_code == 429
    error = held.json()["error"]
    assert error["code"] == "budget_exhausted" and error["retryable"] is True
    assert retry.status_code == 200
    assert model.max_tokens == [40, 25], "B's retry gets 40 less A's 15"
    assert record.reserved_tokens == 0 and record.spent_tokens == 30


# --- The message mapping: OpenAI messages to `ModelMessage`, and the 400 `unsupported_message` ---

TOOL_LOOP: list[dict[str, Any]] = [
    {"role": "system", "content": [{"type": "text", "text": "Be brief."}]},
    {
        "role": "user",
        "name": "sam",
        "content": [{"type": "text", "text": "look up"}, {"type": "text", "text": "SLM"}],
    },
    {
        "role": "assistant",
        "content": None,
        "refusal": None,
        "audio": None,
        "function_call": None,
        "annotations": [],
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "glossary_lookup", "arguments": '{"n": 3}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "SLM: small language model"},
]

TOOL_LOOP_PORT = [
    ModelMessage(role="system", content="Be brief."),
    ModelMessage(role="user", name="sam", content="look up\nSLM"),
    ModelMessage(
        role="assistant",
        content=None,
        tool_calls=[ToolCallRequest(call_id="c1", name="glossary_lookup", arguments={"n": 3})],
    ),
    ModelMessage(role="tool", tool_call_id="c1", content="SLM: small language model"),
]


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
async def test_a_tool_loop_reaches_the_port_intact(stream: bool) -> None:
    ports = _chat_ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    body = {**BODY, "messages": TOOL_LOOP, "stream": stream}
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=body)
    assert res.status_code == 200, res.text
    model = ports.model
    assert isinstance(model, ScriptedModel)
    assert model.calls == [TOOL_LOOP_PORT]
    call = model.calls[0][2].tool_calls
    assert call is not None and type(call[0].arguments["n"]) is int
    assert model.calls[0][2].content is None, "v0 turned a null content into the string 'null'"


def _text(role: str, content: Any = "x", **extra: Any) -> dict[str, Any]:
    return {"role": role, "content": content, **extra}


CALL = {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}

UNSUPPORTED: list[tuple[str, list[dict[str, Any]], str]] = [
    ("developer-role", [_text("developer")], "messages[0].role"),
    ("function-role", [_text("function", name="t")], "messages[0].role"),
    ("missing-role", [{"content": "x"}], "messages[0].role"),
    (
        "image-part",
        [_text("user", [{"type": "image_url", "image_url": {"url": "http://x"}}])],
        "messages[0].content",
    ),
    (
        "mixed-parts",
        [_text("user", [{"type": "text", "text": "a"}, {"type": "input_audio"}])],
        "messages[0].content",
    ),
    ("number-content", [_text("user", 3)], "messages[0].content"),
    ("null-user-content", [_text("user", None)], "messages[0].content"),
    ("missing-user-content", [{"role": "user"}], "messages[0].content"),
    ("null-assistant-no-calls", [_text("user"), _text("assistant", None)], "messages[1].content"),
    (
        "arguments-not-json",
        [
            _text(
                "assistant",
                None,
                tool_calls=[{**CALL, "function": {"name": "t", "arguments": "{nope"}}],
            )
        ],
        "messages[0].tool_calls[0].function.arguments",
    ),
    (
        "arguments-not-object",
        [
            _text(
                "assistant",
                None,
                tool_calls=[{**CALL, "function": {"name": "t", "arguments": "[1]"}}],
            )
        ],
        "messages[0].tool_calls[0].function.arguments",
    ),
    (
        "tool-call-not-function",
        [_text("assistant", None, tool_calls=[{**CALL, "type": "custom"}])],
        "messages[0].tool_calls[0].type",
    ),
    ("tool-calls-on-user", [_text("user", tool_calls=[CALL])], "messages[0].tool_calls"),
    ("tool-without-id", [_text("tool", "result")], "messages[0].tool_call_id"),
    ("tool-without-content", [_text("tool", None, tool_call_id="c1")], "messages[0].content"),
    ("tool-call-id-on-user", [_text("user", tool_call_id="c1")], "messages[0].tool_call_id"),
    (
        "function-call-value",
        [_text("assistant", function_call={"name": "t"})],
        "messages[0].function_call",
    ),
    (
        "unknown-key",
        [_text("user", cache_control={"type": "ephemeral"})],
        "messages[0].cache_control",
    ),
    ("name-not-string", [_text("user", name=3)], "messages[0].name"),
]


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize(
    ("messages", "param"), [(m, p) for _, m, p in UNSUPPORTED], ids=[i for i, _, _ in UNSUPPORTED]
)
async def test_an_unsupported_message_is_400_before_the_model_is_called(
    messages: list[dict[str, Any]], param: str, stream: bool
) -> None:
    ports = _chat_ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    body = {**BODY, "messages": messages, "stream": stream}
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=body)
    assert res.status_code == 400, res.text
    error = res.json()["error"]
    assert error["code"] == "unsupported_message"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == param
    assert error["message"]
    model = ports.model
    assert isinstance(model, ScriptedModel) and model.calls == [], (
        "never a 200 on a changed message"
    )


# --- The body cap: the same `BODY_CAP_BYTES` as the Anthropic proxy route ---

REMOTE_TOKEN = "remote-token-canary-chat-0123456789"  # pragma: allowlist secret (canary)


def _oversized_body() -> bytes:
    """A valid chat body of exactly `BODY_CAP_BYTES + 1` bytes, which the model would answer."""
    empty = json.dumps({**BODY, "messages": [{"role": "user", "content": "simplify: "}]})
    pad = BODY_CAP_BYTES + 1 - len(empty)
    content = "simplify: " + "x" * pad
    raw = json.dumps({**BODY, "messages": [{"role": "user", "content": content}]}).encode()
    assert len(raw) == BODY_CAP_BYTES + 1
    return raw


def _listener(kind: str, app: Any) -> Any:
    return (
        create_proxy_app(app) if kind == "proxy" else create_remote_proxy_app(app, (REMOTE_TOKEN,))
    )


@pytest.mark.parametrize("kind", ["proxy", "remote"])
async def test_chat_proxy_answers_413_without_calling_the_model_when_the_body_is_over_the_cap(
    kind: str,
) -> None:
    """A correlated chat body one byte over the cap is 413 on both listeners; the model is idle."""
    ports = _chat_ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    headers = {"traceparent": TRACEPARENT, "authorization": f"Bearer {REMOTE_TOKEN}"}
    request, ctx = run_request(max_tokens=100)

    async with (
        asgi_client(_listener(kind, app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await client.post(
            "/v1/chat/completions",
            content=_oversized_body(),
            headers={**headers, "content-type": "application/json"},
        )

    assert res.status_code == 413, res.text
    assert res.json()["error"]["code"] == "body_too_large"
    model = ports.model
    assert isinstance(model, ScriptedModel) and model.calls == []
    assert record.model_calls == 0


# --- Model errors: fixed text per code in the body, the upstream text only in the log ---

UPSTREAM_TEXT = "api_base=https://litellm.internal:4000/v1 deployment=big-default-eu said no"


class OutcomeModel:
    """Fails with `http_500`, waits until cancelled, or answers, on every call."""

    name = "outcome"

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.entered = asyncio.Event()

    async def complete(self, messages: Sequence[ModelMessage], **_: Any) -> ModelResult:
        self.entered.set()
        if self.outcome == "error":
            raise ModelError("http_500", UPSTREAM_TEXT)
        if self.outcome == "cancel":
            await asyncio.Event().wait()
        return ModelResult(text="ok", usage=Usage(input_tokens=3, output_tokens=2))

    async def stream(self, messages: Sequence[ModelMessage], **_: Any) -> AsyncIterator[ModelChunk]:
        raise ModelError("http_500", UPSTREAM_TEXT)
        yield ModelChunk()


def _error_of(res: httpx.Response) -> Any:
    """The OpenAI error object of a complete answer, or of a stream's error frame."""
    if res.headers["content-type"].startswith("text/event-stream"):
        return _chunks(res.text)[-2]["error"]
    return res.json()["error"]


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
async def test_chat_proxy_sends_fixed_text_when_the_model_fails(stream: bool) -> None:
    """The error body keeps its code and `retryable` and carries a fixed text, never upstream's."""
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(OutcomeModel("error")))

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json={**BODY, "stream": stream})

    assert _error_of(res) == {
        "message": "the model call failed",
        "type": "model_error",
        "code": "http_500",
        "retryable": False,
    }
    assert "litellm.internal" not in res.text


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
async def test_chat_proxy_logs_the_upstream_text_when_the_model_fails(
    stream: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """The upstream's own text stays at the chassis, in the `chassis.model_proxy` log."""
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(OutcomeModel("error")))
    caplog.set_level(logging.WARNING, logger="chassis.model_proxy")

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        await client.post("/v1/chat/completions", json={**BODY, "stream": stream})

    assert UPSTREAM_TEXT in caplog.text


# --- The hold: a complete call settles its reservation exactly once, on either route ---

ROUTES = {
    "openai": ("/v1/chat/completions", BODY),
    "anthropic": (
        "/v1/messages",
        {"model": "big-default", "max_tokens": 64, "messages": [{"role": "user", "content": "x"}]},
    ),
}


def _count_settles(monkeypatch: pytest.MonkeyPatch) -> list[Usage | None]:
    """Record every `RunRecord.settle` call's usage, and still settle."""
    calls: list[Usage | None] = []
    settle = RunRecord.settle

    def counting(self: RunRecord, held: int, usage: Usage | None) -> bool:
        calls.append(usage)
        return settle(self, held, usage)

    monkeypatch.setattr(RunRecord, "settle", counting)
    return calls


@pytest.mark.parametrize(
    ("outcome", "usage"),
    [("error", None), ("success", Usage(input_tokens=3, output_tokens=2))],
)
@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_complete_call_settles_its_hold_once_when_the_model_answers_or_fails(
    route: str, outcome: str, usage: Usage | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model error settles with no usage, a success with its usage; once each."""
    settles = _count_settles(monkeypatch)
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(OutcomeModel(outcome)))
    path, body = ROUTES[route]
    request, ctx = run_request(max_tokens=100)

    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        await client.post(path, json=body, headers={"traceparent": TRACEPARENT})

    assert settles == [usage]
    assert record.reserved_tokens == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_complete_call_settles_its_hold_once_when_it_is_cancelled(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel while the model works settles the hold with no usage, once."""
    settles = _count_settles(monkeypatch)
    model = OutcomeModel("cancel")
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(model))
    path, body = ROUTES[route]
    request, ctx = run_request(max_tokens=100)

    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        call = asyncio.create_task(
            client.post(path, json=body, headers={"traceparent": TRACEPARENT})
        )
        await asyncio.wait_for(model.entered.wait(), timeout=5)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    assert settles == [None]
    assert record.reserved_tokens == 0


async def test_chat_proxy_charges_nothing_when_a_correlated_stream_is_abandoned() -> None:
    """Known gap 004 G-2 stays: only the Anthropic proxy route charges an abandoned stream."""
    model = GatedModel()
    body = {**ANTHROPIC_BODY, "max_tokens": 30, "stream": True}

    record = await abandon_stream(model, "/v1/chat/completions", body, b'"content": "first"')

    assert (record.spent_tokens, record.reserved_tokens) == (0, 0)


@pytest.mark.parametrize("code", ["http_401", "http_403"])
async def test_chat_proxy_answers_403_model_route_denied_when_the_upstream_answers_401_or_403(
    code: str,
) -> None:
    """An upstream 401 or 403 is a 403 `model_route_denied` in the OpenAI error shape."""
    model = FailingModel(ModelError(code, "denied"))
    app = create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(model))

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=ANTHROPIC_BODY)

    assert res.status_code == 403, res.text
    assert res.json() == {
        "error": {
            "message": "this model route is not allowed for this service",
            "type": "permission_error",
            "code": "model_route_denied",
            "retryable": False,
        }
    }
