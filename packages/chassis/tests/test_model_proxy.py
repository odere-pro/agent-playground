"""The model pass-through: `POST /v1/chat/completions` on the proxy app maps the OpenAI shape to
`ModelPort` and back, streaming and complete, forwards no `Authorization` header, and counts calls
per route. A call whose `traceparent` names an in-flight run gets a span and is charged to that
run's budget; an exhausted run is refused; a call without one is served and counted uncorrelated.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest
from chassis import CHASSIS_VERSION
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.model import (
    ModelChunk,
    ModelMessage,
    ModelPort,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from chassis.server import ChassisConfig, create_app
from chassis.server.model_proxy import DEFAULT_UNCORRELATED_MAX_TOKENS
from chassis.server.proxy_app import create_proxy_app

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


def _ports(model: ModelPort | None = None) -> PortBundle:
    return PortBundle(
        model=model
        or ScriptedModel(
            [
                ScriptRule(
                    match="lookup", tool_call=ToolCallRequest(call_id="c1", name="glossary_lookup")
                ),
                ScriptRule(match="fail", error="upstream_down", retryable=True),
                ScriptRule(match="simplify", reply="Plain words. Short sentences."),
            ]
        ),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _chunks(text: str) -> list[Any]:
    out: list[Any] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


async def test_complete_maps_the_openai_shape_both_ways() -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
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
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
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
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    body = {
        **BODY,
        "messages": [{"role": "user", "content": "lookup SLM"}],
        "tools": [{"type": "function", "function": {"name": "glossary_lookup", "parameters": {}}}],
    }
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
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
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    body = {**BODY, "messages": [{"role": "user", "content": "fail"}]}
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
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
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": "Bearer leaked-key"}
        )
    assert res.status_code == 200
    assert upstream and "authorization" not in {k.lower() for k in upstream[0].headers}
    assert "leaked-key" not in res.text


async def test_not_ready_is_503() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    async with _client(create_proxy_app(app)) as client:
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 503


TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"


def _run_ctx(max_tokens: int) -> tuple[Request, Context]:
    request = Request(
        request_id="req-1",
        trace_id=TRACE,
        idempotency_key="idem-1",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="x"),
        budget=Budget(max_tokens=max_tokens),
    )
    ctx = Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=Versions(chassis=CHASSIS_VERSION),
    )
    return request, ctx


async def test_a_call_of_an_in_flight_run_gets_a_span_and_is_charged() -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    headers = {"traceparent": TRACEPARENT}
    request, ctx = _run_ctx(max_tokens=100)
    async with (
        _client(create_proxy_app(app)) as client,
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
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = _run_ctx(max_tokens=100)
    async with (
        _client(create_proxy_app(app)) as client,
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
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    headers = {"traceparent": TRACEPARENT}
    request, ctx = _run_ctx(max_tokens=15)
    async with (
        _client(create_proxy_app(app)) as client,
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
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    body = {**BODY, "messages": [{"role": "user", "content": "fail"}], "stream": True}
    request, ctx = _run_ctx(max_tokens=100)
    async with (
        _client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await client.post(
            "/v1/chat/completions", json=body, headers={"traceparent": TRACEPARENT}
        )
    assert _chunks(res.text)[-2]["error"]["code"] == "upstream_down"
    assert record.model_calls == 1 and record.spent_tokens == 0


# --- The forwarded `max_tokens`: capped at what the run has left, reserved while the call runs ---


class RecordingModel(ScriptedModel):
    """`ScriptedModel` that also records the `max_tokens` each call was given, and can hold a
    stream open until `gate` is set.
    """

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        super().__init__([ScriptRule(match="simplify", reply="Plain words.")])
        self.max_tokens: list[int | None] = []
        self.gate = gate
        self.streaming = asyncio.Event()

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.max_tokens.append(max_tokens)
        return await super().complete(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        self.max_tokens.append(max_tokens)
        self.streaming.set()
        if self.gate is not None:
            await asyncio.wait_for(self.gate.wait(), timeout=5)
        async for chunk in super().stream(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            yield chunk


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
    model = RecordingModel()
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    body: dict[str, Any] = {**BODY, "stream": stream}
    body.pop("max_tokens")
    if asked is not None:
        body["max_tokens"] = asked
    request, ctx = _run_ctx(max_tokens=20)
    async with (
        _client(create_proxy_app(app)) as client,
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
    model = RecordingModel()
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    body = {**BODY, "max_tokens": asked}
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        assert (await client.post("/v1/chat/completions", json=body)).status_code == 200
    assert model.max_tokens == [DEFAULT_UNCORRELATED_MAX_TOKENS if asked is None else asked]


async def test_a_concurrent_call_cannot_be_given_tokens_another_call_holds() -> None:
    """Call A streams with no `max_tokens`, so it holds the run's whole remainder until its
    usage is known. Call B of the same run, sent meanwhile, is refused with 429 and
    `retryable: true` (the tokens are held, not spent). Once A settles, B's retry gets what A
    left.
    """
    gate = asyncio.Event()
    model = RecordingModel(gate)
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    headers = {"traceparent": TRACEPARENT}
    unset = {k: v for k, v in BODY.items() if k != "max_tokens"}
    request, ctx = _run_ctx(max_tokens=40)
    async with (
        _client(create_proxy_app(app)) as client,
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
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    body = {**BODY, "messages": TOOL_LOOP, "stream": stream}
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
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
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    body = {**BODY, "messages": messages, "stream": stream}
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
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
