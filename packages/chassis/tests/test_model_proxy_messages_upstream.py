"""The Anthropic proxy route (proxy port, `POST /v1/messages`): the upstream side.

Streams end at a deadline, the upstream stream is closed when the response ends early, and no
inbound header or query reaches the model or the LiteLLM adapter's upstream request. Driven in
process, no socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from chassis.adapters.litellm import LiteLLMModel
from chassis.ports.model import ModelChunk, ModelMessage, ModelResult
from chassis.server import ChassisConfig, create_app
from chassis.server import model_proxy_messages as route_module
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    GLOSSARY_RULES,
    PROXY_CONFIG,
    TRACEPARENT,
    GatedModel,
    RecordingModel,
    asgi_client,
    frame_names,
    proxy_ports,
    run_request,
    sse_frames,
    stream_and_leave,
    telemetry_of,
)

STREAM_30 = {**ANTHROPIC_BODY, "max_tokens": 30, "stream": True}
CLOSE_WAIT_S = 1.0
"""How long a test waits for the route's background close of the upstream stream."""


class ExplicitlyClosedStream:
    """An upstream stream object (not a generator): only an explicit `aclose` marks it closed."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.closed = 0
        self.was_closed = asyncio.Event()
        self._sent = 0

    def __aiter__(self) -> ExplicitlyClosedStream:
        return self

    async def __anext__(self) -> ModelChunk:
        self._sent += 1
        if self._sent == 1:
            return ModelChunk(text="first")
        await self.gate.wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed += 1
        self.was_closed.set()


class ExplicitlyClosedModel:
    """A model whose `stream` returns one `ExplicitlyClosedStream`."""

    name = "closable"

    def __init__(self) -> None:
        self.upstream = ExplicitlyClosedStream()

    async def complete(self, messages: Sequence[ModelMessage], **_: Any) -> ModelResult:
        raise AssertionError("not used")

    def stream(self, messages: Sequence[ModelMessage], **_: Any) -> ExplicitlyClosedStream:
        return self.upstream


class RecordingRouter:
    """A LiteLLM router stand-in: records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if json.loads(request.content).get("stream"):
            chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}}]}
            usage = {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            text = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(usage)}\n\ndata: [DONE]\n\n"
            return httpx.Response(
                200, content=text.encode(), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )


async def test_a_correlated_stream_ends_with_a_timeout_frame_when_the_runs_deadline_passes() -> (
    None
):
    """At the run's wall clock the stream ends with a `timeout_error` frame, the upstream is
    closed, and the run is charged the reservation."""
    model = GatedModel()
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))
    request, ctx = run_request(max_tokens=1000)
    request.budget.timeout_ms = 300  # the run's wall clock
    ctx.budget.timeout_ms = 300

    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await asyncio.wait_for(
            client.post("/v1/messages", json=STREAM_30, headers={"traceparent": TRACEPARENT}), 5
        )

    frames = sse_frames(res.text)
    names = frame_names(frames)
    assert res.status_code == 200 and names[-1] == "error" and "message_stop" not in names
    assert frames[-1][1]["error"] == {
        "type": "timeout_error",
        "message": "model_timeout: the model route did not answer in time",
    }
    assert model.closed, "the upstream stream was closed"
    assert (record.spent_tokens, record.reserved_tokens) == (30, 0)


async def test_an_uncorrelated_stream_ends_with_an_error_frame_when_the_default_deadline_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no run, the stream ends at the uncorrelated deadline and the cap is charged."""
    monkeypatch.setattr(route_module, "UNCORRELATED_STREAM_DEADLINE_S", 0.2)
    model = GatedModel()
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await asyncio.wait_for(client.post("/v1/messages", json=STREAM_30), 5)
        cap = app.state.uncorrelated_cap

    assert sse_frames(res.text)[-1][0] == "error"
    assert model.closed and (cap.spent, cap.reserved) == (30, 0)
    assert route_module.UNCORRELATED_STREAM_DEADLINE_S == 0.2


def test_the_uncorrelated_stream_deadline_is_five_minutes_when_not_patched() -> None:
    """The default uncorrelated stream deadline is 300 s."""
    assert route_module.UNCORRELATED_STREAM_DEADLINE_S == 300.0


async def test_the_upstream_stream_is_closed_once_when_the_client_leaves() -> None:
    """A client that leaves after the first text chunk gets the upstream closed exactly once."""
    model = ExplicitlyClosedModel()
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))

    async with app.router.lifespan_context(app):
        try:
            await stream_and_leave(
                create_proxy_app(app), "/v1/messages", STREAM_30, b"text_delta", {}
            )
            async with asyncio.timeout(CLOSE_WAIT_S):  # the close runs as its own task
                await model.upstream.was_closed.wait()
            closed = model.upstream.closed
        finally:
            model.upstream.gate.set()

    assert closed == 1


async def test_the_upstream_stream_is_closed_once_when_the_stream_deadline_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream cut at the uncorrelated deadline gets the upstream closed exactly once."""
    monkeypatch.setattr(route_module, "UNCORRELATED_STREAM_DEADLINE_S", 0.2)
    model = ExplicitlyClosedModel()
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))

    async with app.router.lifespan_context(app):
        try:
            async with asgi_client(create_proxy_app(app)) as client:
                await asyncio.wait_for(client.post("/v1/messages", json=STREAM_30), 5)
            async with asyncio.timeout(CLOSE_WAIT_S):  # the close runs as its own task
                await model.upstream.was_closed.wait()
            closed = model.upstream.closed
        finally:
            model.upstream.gate.set()

    assert closed == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_the_adapters_upstream_request_carries_only_its_own_key_when_the_caller_sends_some(
    stream: bool,
) -> None:
    """What the LiteLLM adapter actually sends upstream: its own key, never the caller's."""
    router = RecordingRouter()
    adapter = LiteLLMModel(
        "http://router/v1", "chassis-own-key", transport=httpx.MockTransport(router)
    )
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(adapter))
    leaked = {
        "authorization": "Bearer caller-secret-1",
        "x-api-key": "caller-secret-2",
        "anthropic-version": "caller-secret-3",
        "anthropic-beta": "caller-secret-4",
        "x-claude-code-session-id": "caller-secret-5",
        "traceparent": TRACEPARENT,
    }

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/messages?beta=true", json={**ANTHROPIC_BODY, "stream": stream}, headers=leaked
        )

    assert res.status_code == 200, res.text
    assert len(router.requests) == 1
    sent = router.requests[0]
    names = {k.lower() for k in sent.headers}
    assert not names & {"x-api-key", "anthropic-version", "anthropic-beta", "traceparent"}
    assert "x-claude-code-session-id" not in names
    assert sent.headers["authorization"] == "Bearer chassis-own-key"
    everything = repr(sent.headers.raw) + sent.content.decode() + str(sent.url)
    assert "caller-secret" not in everything and "beta=true" not in everything


async def test_no_inbound_header_reaches_telemetry_or_the_reply_when_the_caller_sends_keys() -> (
    None
):
    """Inbound credentials and Anthropic headers stay out of spans and the reply; a query is
    ignored."""
    ports = proxy_ports(RecordingModel(GLOSSARY_RULES))
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)
    headers = {
        "authorization": "Bearer should-not-travel",
        "x-api-key": "should-not-travel",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "x",
    }

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages?beta=true&x=1", json=ANTHROPIC_BODY, headers=headers)

    assert res.status_code == 200
    assert "should-not-travel" not in repr(telemetry_of(ports).spans) + res.text + repr(res.headers)
