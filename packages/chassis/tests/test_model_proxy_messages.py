"""The Anthropic proxy route (`POST /v1/messages` on the proxy port; contract v5, part A).

Driven with `httpx.ASGITransport` and a scripted model, no socket. Covers the captured body
shape, the tool round trip, admission, the review corrections of 2026-10-09 (abandoned streams,
fixed upstream text, credential canaries, the body cap, empty turns, booleans, `count_tokens`),
and the 403 for a refused model route on both proxy routes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, MutableMapping, Sequence
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from chassis import CHASSIS_VERSION
from chassis.adapters.anthropic_compat.types import Message, RawMessageStreamEvent
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from chassis.server import ChassisConfig, create_app
from chassis.server import model_proxy_messages as route_module
from chassis.server.correlation import RunRecord
from chassis.server.model_proxy_messages import FIRST_CHUNK_WAIT_S
from chassis.server.proxy_app import create_proxy_app
from chassis.server.remote_auth import create_remote_proxy_app
from pydantic import TypeAdapter

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret (a trace id in a test)
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
REMOTE_TOKEN = "remote-token-canary-0123456789abcdef"  # pragma: allowlist secret (canary)
API_KEY_CANARY = "xapikey-canary-fedcba9876543210"  # pragma: allowlist secret (canary)
CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
BODY: dict[str, Any] = {
    "model": "big-default",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "simplify: the quick brown fox"}],
}
EVENT: TypeAdapter[Any] = TypeAdapter(RawMessageStreamEvent)


class RecordingModel(ScriptedModel):
    """A `ScriptedModel` that also records tools, `max_tokens`, and temperature."""

    def __init__(self, rules: Sequence[ScriptRule] | None = None) -> None:
        super().__init__(rules)
        self.tools: list[Sequence[ToolSpec] | None] = []
        self.max_tokens: list[int | None] = []
        self.temperatures: list[float] = []

    def _note(self, tools: Sequence[ToolSpec] | None, max_tokens: int | None, temp: float) -> None:
        self.tools.append(tools)
        self.max_tokens.append(max_tokens)
        self.temperatures.append(temp)

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self._note(tools, max_tokens, temperature)
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
        self._note(tools, max_tokens, temperature)
        async for chunk in super().stream(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            yield chunk


class FailingModel:
    """Raises a `ModelError` on every call, before any chunk."""

    name = "failing"

    def __init__(self, error: ModelError) -> None:
        self.error = error

    async def complete(self, messages: Sequence[ModelMessage], **_: Any) -> ModelResult:
        raise self.error

    async def stream(self, messages: Sequence[ModelMessage], **_: Any) -> AsyncIterator[ModelChunk]:
        raise self.error
        yield ModelChunk()


class GatedModel:
    """Streams one chunk, then waits for `gate`. `first_usage` rides on the first chunk."""

    name = "gated"

    def __init__(self, first_usage: Usage | None = None, silent: bool = False) -> None:
        self.gate = asyncio.Event()
        self.first_usage = first_usage
        self.silent = silent
        self.max_tokens: list[int | None] = []

    async def complete(self, messages: Sequence[ModelMessage], **_: Any) -> ModelResult:
        await self.gate.wait()
        return ModelResult(text="late")

    async def stream(
        self, messages: Sequence[ModelMessage], *, max_tokens: int | None = None, **_: Any
    ) -> AsyncIterator[ModelChunk]:
        self.max_tokens.append(max_tokens)
        if not self.silent:
            yield ModelChunk(text="first", usage=self.first_usage)
        await self.gate.wait()
        yield ModelChunk(usage=Usage(input_tokens=1, output_tokens=1), finish=True)


RULES = [
    ScriptRule(
        match="lookup",
        tool_call=ToolCallRequest(
            call_id="call_1", name="mcp__glossary__glossary_lookup", arguments={"term": "SLM"}
        ),
        usage=Usage(input_tokens=812, output_tokens=31),
    ),
    ScriptRule(after_tool=True, reply="SLM is a small language model."),
    ScriptRule(match="simplify", reply="Plain words. Short sentences."),
]


def _ports(model: Any = None) -> PortBundle:
    return PortBundle(
        model=model or RecordingModel(RULES),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _run(max_tokens: int = 40000) -> tuple[Request, Context]:
    request = Request(
        request_id="req-msgs",
        trace_id=TRACE,
        idempotency_key="idem-msgs",
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


def _frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), block
        out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


def _validate(frames: list[tuple[str, dict[str, Any]]]) -> None:
    """Every frame is a valid Anthropic stream event (`ping` is outside the SDK's union)."""
    for name, data in frames:
        if name != "ping":
            EVENT.validate_python(data)


def _telemetry(ports: PortBundle) -> InMemoryTelemetry:
    assert isinstance(ports.telemetry, InMemoryTelemetry)
    return ports.telemetry


# ---- the captured body shape --------------------------------------------------------------------

CAPTURED: dict[str, Any] = {
    "model": "big-default",
    "max_tokens": 32000,
    "stream": True,
    "system": [
        {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.294; cch=abc;"},
        {
            "type": "text",
            "text": "You are a Claude agent.",
            "cache_control": {"type": "ephemeral"},
        },
    ],
    "messages": [
        {"role": "system", "content": [{"type": "text", "text": "# Environment\ncwd: /work"}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "lookup SLM",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ],
    "tools": [
        {
            "name": "mcp__glossary__glossary_lookup",
            "description": "Look up a term.",
            "input_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"term": {"type": "string"}},
                "required": ["term"],
            },
        }
    ],
    "thinking": {"type": "adaptive", "display": "updates"},
    "output_config": {"effort": "high"},
    "context_management": {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
    "metadata": {"user_id": '{"device_id":"d","account_uuid":"a","session_id":"s"}'},
    "safeguards": {"context": {"cwd": "/home/someone/work"}},
}


async def test_the_captured_body_streams_a_tool_use_round_trip() -> None:
    """Replay of the body shape the real CLI sent (`notes/capture`): `role: system` in
    `messages`, `system` blocks with `cache_control`, `thinking`, `output_config`,
    `context_management`, `metadata`, `safeguards`, and a tool with `$schema`."""
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = _run()
    headers = {"traceparent": TRACEPARENT, "anthropic-version": "2023-06-01"}
    async with (
        _client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await client.post("/v1/messages?beta=true", json=CAPTURED, headers=headers)
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.headers["cache-control"] == "no-cache"
        assert res.headers["request-id"].startswith("req_")
        frames = _frames(res.text)
        # Turn 2: the client posts the assistant tool_use and a user tool_result.
        second = {
            **CAPTURED,
            "messages": [
                CAPTURED["messages"][1],
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "mcp__glossary__glossary_lookup",
                            "input": {"term": "SLM"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": [{"type": "text", "text": "small language model"}],
                            "is_error": False,
                        }
                    ],
                },
            ],
        }
        res2 = await client.post("/v1/messages?beta=true", json=second, headers=headers)
    names = [name for name, _ in frames]
    assert names == [
        "message_start",
        "ping",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ], names
    _validate(frames)
    assert frames[0][1]["message"]["model"] == "big-default"
    start, delta = frames[2][1], frames[3][1]
    assert start["content_block"] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "mcp__glossary__glossary_lookup",
        "input": {},
    }
    assert delta["delta"] == {"type": "input_json_delta", "partial_json": '{"term":"SLM"}'}
    assert frames[5][1]["delta"]["stop_reason"] == "tool_use"
    assert frames[5][1]["usage"] == {"input_tokens": 812, "output_tokens": 31}

    model = ports.model
    assert isinstance(model, RecordingModel)
    assert model.calls[0] == [
        ModelMessage(role="system", content="You are a Claude agent."),
        ModelMessage(role="system", content="# Environment\ncwd: /work"),
        ModelMessage(role="user", content="lookup SLM"),
    ]
    sent = model.tools[0]
    assert sent is not None and sent[0].parameters == {
        "type": "object",
        "properties": {"term": {"type": "string"}},
        "required": ["term"],
    }
    assert model.max_tokens[0] == 32000 and model.temperatures[0] == 0.0

    assert res2.status_code == 200, res2.text
    assert model.calls[1][-2:] == [
        ModelMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCallRequest(
                    call_id="call_1",
                    name="mcp__glossary__glossary_lookup",
                    arguments={"term": "SLM"},
                )
            ],
        ),
        ModelMessage(role="tool", tool_call_id="call_1", content="small language model"),
    ]
    frames2 = _frames(res2.text)
    assert frames2[-2][1]["delta"]["stop_reason"] == "end_turn"
    assert record.model_calls == 2
    telemetry = _telemetry(ports)
    for param in ("thinking", "output_config.effort", "context_management", "safeguards"):
        assert telemetry.counter_value(
            "chassis.model_proxy.ignored", format="anthropic", param=param
        )
    assert telemetry.counter_value(
        "chassis.model_proxy.ignored", format="anthropic", param="system.billing_header"
    )
    assert not any("metadata" in repr(k) for k in telemetry.counters)
    assert any(
        s.name == "chassis.model.call" and s.attributes.get("format") == "anthropic"
        for s in telemetry.spans
    )


async def test_the_complete_form_round_trips_and_validates_as_a_message() -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    body = {**CAPTURED, "stream": False, "max_tokens": 64}  # 32000 is over the cap
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body, headers={"accept": "application/json"})
        plain = await client.post("/v1/messages", json={**BODY, "stream": True}, headers={})
    assert res.status_code == 200, res.text
    Message.model_validate(res.json())
    assert res.json()["stop_reason"] == "tool_use"
    assert res.json()["content"] == [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "mcp__glossary__glossary_lookup",
            "input": {"term": "SLM"},
        }
    ]
    assert res.json()["usage"] == {"input_tokens": 812, "output_tokens": 31}
    # accept does not pick the form: stream true is SSE, with or without an accept header.
    assert plain.headers["content-type"].startswith("text/event-stream")


async def test_a_stream_with_text_then_a_tool_call_keeps_the_indexes_in_order() -> None:
    model = ScriptedModel(
        [
            ScriptRule(
                match="go",
                reply="Looking. ",
                tool_call=ToolCallRequest(call_id="c9", name="t", arguments={"k": 1}),
            )
        ]
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    body = {**BODY, "messages": [{"role": "user", "content": "go"}], "stream": True}
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body)
    frames = _frames(res.text)
    indexes = [d["index"] for n, d in frames if n == "content_block_start"]
    assert indexes == [0, 1]
    assert [n for n, _ in frames][-3:] == ["content_block_stop", "message_delta", "message_stop"]
    _validate(frames)


# ---- correction 1: an abandoned stream is charged (this route only) -----------------------------


async def _stream_and_leave(
    proxy: Any, path: str, body: dict[str, Any], marker: bytes, headers: dict[str, str]
) -> list[Any]:
    """POST over raw ASGI; the client leaves once `marker` has been sent."""
    raw = json.dumps(body).encode()
    gone = asyncio.Event()
    sent: list[Any] = []
    first = True

    async def receive() -> dict[str, Any]:
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)
        if marker in message.get("body", b""):
            gone.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
            *[(k.encode(), v.encode()) for k, v in headers.items()],
        ],
        "client": ("127.0.0.1", 1),
        "server": ("chassis", 80),
        "app": proxy,
    }
    await asyncio.wait_for(proxy(scope, receive, send), 5)
    return sent


async def _abandon(
    model: GatedModel, path: str, body: dict[str, Any], marker: bytes
) -> tuple[RunRecord, GatedModel]:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    proxy = create_proxy_app(app)
    request, ctx = _run(max_tokens=1000)
    async with (
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        try:
            sent = await _stream_and_leave(proxy, path, body, marker, {"traceparent": TRACEPARENT})
            assert sent[0]["status"] == 200
        finally:
            model.gate.set()
        await asyncio.sleep(0)
        return record, model


async def test_an_abandoned_messages_stream_is_charged_its_reservation_when_usage_is_unknown() -> (
    None
):
    model = GatedModel()
    body = {**BODY, "max_tokens": 30, "stream": True}
    record, _ = await _abandon(model, "/v1/messages", body, b"text_delta")
    assert model.max_tokens == [30]
    assert (record.spent_tokens, record.reserved_tokens) == (30, 0)


async def test_an_abandoned_messages_stream_is_charged_its_last_known_usage() -> None:
    model = GatedModel(first_usage=Usage(input_tokens=3, output_tokens=4))
    body = {**BODY, "max_tokens": 30, "stream": True}
    record, _ = await _abandon(model, "/v1/messages", body, b"text_delta")
    assert (record.spent_tokens, record.reserved_tokens) == (7, 0)


async def test_a_messages_stream_abandoned_in_the_hold_window_is_charged_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(route_module, "FIRST_CHUNK_WAIT_S", 0.05)
    model = GatedModel(silent=True)  # no chunk before the gate: the window passes
    body = {**BODY, "max_tokens": 30, "stream": True}
    record, _ = await _abandon(model, "/v1/messages", body, b"message_start")
    assert (record.spent_tokens, record.reserved_tokens) == (30, 0)


async def test_the_chat_route_is_unchanged_an_abandoned_correlated_stream_is_not_charged() -> None:
    """Known gap 004 G-2 stays: the change is on the Anthropic proxy route only."""
    model = GatedModel()
    body = {**BODY, "max_tokens": 30, "stream": True}
    record, _ = await _abandon(model, "/v1/chat/completions", body, b'"content": "first"')
    assert (record.spent_tokens, record.reserved_tokens) == (0, 0)


def test_the_hold_window_constant_is_the_drafts() -> None:
    assert FIRST_CHUNK_WAIT_S == 5.0 and route_module.PING_INTERVAL_S == 15.0


# ---- correction 2 and 3: fixed text, and no credential anywhere ---------------------------------

UPSTREAM_TEXT = "context length 99999 exceeded (upstream-detail-marker)"


@pytest.mark.parametrize("stream", [False, True])
async def test_model_rejected_request_sends_fixed_text_and_logs_the_upstream_text(
    stream: bool, caplog: pytest.LogCaptureFixture
) -> None:
    model = FailingModel(ModelError("http_400", UPSTREAM_TEXT))
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    caplog.set_level(logging.DEBUG)
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**BODY, "stream": stream})
    assert res.status_code == 400, res.text
    assert "upstream-detail-marker" not in res.text and "99999" not in res.text
    assert res.json()["error"] == {
        "type": "invalid_request_error",
        "message": "model_rejected_request: the model route rejected the request",
    }
    assert res.headers["x-should-retry"] == "false"
    assert "upstream-detail-marker" in caplog.text  # the operator still sees it


async def test_a_mid_stream_model_error_sends_fixed_text_in_the_error_frame() -> None:
    class Late:
        name = "late"

        async def stream(self, messages: Sequence[ModelMessage], **_: Any) -> AsyncIterator[Any]:
            yield ModelChunk(text="partial")
            raise ModelError("http_400", UPSTREAM_TEXT)

    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(Late()))
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**BODY, "stream": True})
    assert res.status_code == 200
    frames = _frames(res.text)
    assert frames[-1][0] == "error" and "message_stop" not in [n for n, _ in frames]
    assert "upstream-detail-marker" not in res.text
    assert frames[-1][1]["error"]["message"].startswith("model_rejected_request: ")


@pytest.mark.parametrize("code", ["http_401", "http_403"])
@pytest.mark.parametrize("stream", [False, True])
async def test_an_upstream_401_or_403_is_403_model_route_denied_on_the_messages_route(
    code: str, stream: bool
) -> None:
    ports = _ports(FailingModel(ModelError(code, "Received API Key = sk-abcdef12345678")))
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**BODY, "stream": stream})
    assert res.status_code == 403, res.text
    assert res.json()["error"] == {
        "type": "permission_error",
        "message": "model_route_denied: this model route is not allowed for this service",
    }
    assert "sk-abcdef" not in res.text
    assert (
        _telemetry(ports).counter_value("chassis.model_upstream_denied", route="big-default") == 1
    )


@pytest.mark.parametrize("code", ["http_401", "http_403"])
async def test_an_upstream_401_or_403_is_403_model_route_denied_on_the_chat_route(
    code: str,
) -> None:
    app = create_app(
        ChassisConfig.model_validate(CONFIG), _ports(FailingModel(ModelError(code, "denied")))
    )
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=BODY)
    assert res.status_code == 403, res.text
    assert res.json() == {
        "error": {
            "message": "this model route is not allowed for this service",
            "type": "permission_error",
            "code": "model_route_denied",
            "retryable": False,
        }
    }


async def test_the_credentials_appear_nowhere_on_the_messages_route(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Canary: the remote token and an `x-api-key` value reach no log record, no span attribute,
    no counter label, and no response body, 400 bodies that echo request content included."""
    ports = _ports()
    public = create_app(ChassisConfig.model_validate(CONFIG), ports)
    remote = create_remote_proxy_app(public, (REMOTE_TOKEN,))
    request, ctx = _run()
    headers = {
        "authorization": f"Bearer {REMOTE_TOKEN}",
        "x-api-key": API_KEY_CANARY,
        "traceparent": TRACEPARENT,
    }
    echoing = [
        # A tool_result with an unknown id, and the canaries in every echoable place.
        {
            **BODY,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": API_KEY_CANARY, "content": "x"},
                    ],
                }
            ],
        },
        {**BODY, "messages": [{"role": REMOTE_TOKEN, "content": API_KEY_CANARY}]},
        {**BODY, "messages": [{"role": "user", "content": [{"type": API_KEY_CANARY}]}]},
        {**BODY, "tools": [{"type": REMOTE_TOKEN, "name": "n"}]},
        {**BODY, "max_tokens": API_KEY_CANARY},
        {**BODY, "extra_" + REMOTE_TOKEN: API_KEY_CANARY},
    ]
    caplog.set_level(logging.DEBUG)
    bodies: list[str] = []
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        for body in echoing:
            res = await client.post("/v1/messages", json=body, headers=headers)
            bodies.append(res.text + repr(res.headers))
        ok = await client.post("/v1/messages", json={**BODY, "stream": True}, headers=headers)
        bodies.append(ok.text + repr(ok.headers))
        # Refusals by the middleware, with the canary in the wrong places.
        bad = await client.post(
            "/v1/messages",
            json=BODY,
            headers={"x-api-key": REMOTE_TOKEN, "traceparent": TRACEPARENT},
        )
        bodies.append(bad.text + repr(bad.headers))
        wrong = await client.post(
            "/v1/messages",
            json=BODY,
            headers={"authorization": f"Bearer {API_KEY_CANARY}x", "x-api-key": API_KEY_CANARY},
        )
        bodies.append(wrong.text + repr(wrong.headers))
        assert ok.status_code == 200 and bad.status_code == 401 and wrong.status_code == 401
    telemetry = _telemetry(ports)
    surfaces = [
        *bodies,
        caplog.text,
        repr(telemetry.logs),
        repr([s.attributes for s in telemetry.spans]),
        repr(list(telemetry.counters)),
    ]
    assert len(telemetry.spans) >= 1
    for canary in (REMOTE_TOKEN, API_KEY_CANARY):
        for surface in surfaces:
            assert canary not in surface


# ---- correction 4: the body cap ------------------------------------------------------------------


async def test_a_body_over_the_cap_is_413_before_it_is_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chassis.adapters.anthropic_compat.model_wire import BODY_CAP_BYTES

    def must_not_parse(raw: Any) -> Any:
        raise AssertionError("parsed an oversized body")

    monkeypatch.setattr(route_module, "parse_messages_request", must_not_parse)
    monkeypatch.setattr(route_module, "json", SimpleNamespace(loads=must_not_parse))
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    piece = b"x" * (256 * 1024)
    total = BODY_CAP_BYTES // len(piece) * 4
    read = 0

    async def chunked() -> AsyncIterator[bytes]:  # no Content-Length: the cap acts while reading
        nonlocal read
        for _ in range(total):
            read += 1
            yield piece

    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        streamed = await client.post("/v1/messages", content=chunked())
        declared = await client.post("/v1/messages", content=b"x" * (BODY_CAP_BYTES + 1))
    assert BODY_CAP_BYTES == 4 * 1024 * 1024
    for res in (streamed, declared):
        assert res.status_code == 413, res.text
        assert res.json()["error"]["type"] == "request_too_large"
        assert res.json()["type"] == "error" and res.headers["request-id"].startswith("req_")
    assert read <= BODY_CAP_BYTES // len(piece) + 2, f"read {read} of {total} pieces"


async def test_a_body_at_the_cap_is_read_and_parsed() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", content=b"not json")
    assert res.status_code == 400
    assert res.json()["error"]["message"].startswith("invalid_body: ")


# ---- correction 5: empty turns over the wire ----------------------------------------------------


async def test_empty_turns_are_skipped_or_refused_and_never_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    ok_body = {
        **BODY,
        "system": [{"type": "text", "text": ""}],
        "messages": [
            {"role": "system", "content": ""},
            {"role": "user", "content": "simplify: a"},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}]},
            {"role": "user", "content": "simplify: b"},
        ],
    }
    empty_user = {**BODY, "messages": [{"role": "user", "content": []}]}
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        ok = await client.post("/v1/messages", json=ok_body)
        refused = await client.post("/v1/messages", json=empty_user)
        from chassis.adapters.anthropic_compat import model_wire

        def invalid(raw: Any) -> Any:
            ModelMessage(role="tool")
            raise AssertionError("unreachable")

        monkeypatch.setattr(model_wire, "_parse", invalid)
        broken = await client.post("/v1/messages", json=BODY)
    assert ok.status_code == 200, ok.text
    model = ports.model
    assert isinstance(model, RecordingModel)
    assert model.calls[0] == [
        ModelMessage(role="user", content="simplify: a"),
        ModelMessage(role="user", content="simplify: b"),
    ]
    assert refused.status_code == 400
    assert refused.json()["error"] == {
        "type": "invalid_request_error",
        "message": "invalid_body: messages[0].content: a user turn needs content",
    }
    assert broken.status_code == 400, broken.text
    telemetry = _telemetry(ports)
    assert telemetry.counter_value("chassis.model_calls", route="big-default") == 1


# ---- correction 6 is a doc; correction 7: booleans, ids, count_tokens ---------------------------


@pytest.mark.parametrize("key", ["max_tokens", "temperature"])
async def test_a_json_boolean_is_a_400_on_the_wire(key: str) -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**BODY, key: True})
    assert res.status_code == 400
    assert res.json()["error"]["message"].startswith(f"invalid_body: {key}: ")
    assert _telemetry(ports).counter_value("chassis.model_calls", route="big-default") == 0


async def test_a_tool_result_with_no_preceding_tool_use_is_a_400_with_no_echo() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    body = {
        **BODY,
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "nope-1"}]}
        ],
    }
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body)
    assert res.status_code == 400 and "nope-1" not in res.text
    assert res.json()["error"]["message"].startswith("unsupported_message: ")


@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_count_tokens_is_404_in_the_anthropic_error_shape(method: str) -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.request(method, "/v1/messages/count_tokens", json=BODY)
        other = await client.post("/v1/models", json={})
    assert res.status_code == 404
    assert res.json()["type"] == "error"
    assert res.json()["error"]["type"] == "not_found_error"
    assert res.headers["request-id"] == res.json()["request_id"]
    assert other.json() == {"detail": "Not Found"}  # every other 404 keeps FastAPI's body


# ---- admission, shared with the chat route ------------------------------------------------------


async def test_a_correlated_call_is_charged_to_its_run_and_clamped_to_what_is_left() -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = _run(max_tokens=100)
    headers = {"traceparent": TRACEPARENT}
    async with (
        _client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await client.post("/v1/messages", json={**BODY, "max_tokens": 32000}, headers=headers)
        assert res.status_code == 200
        spent = record.spent_tokens
        again = await client.post("/v1/messages", json=BODY, headers=headers)
    model = ports.model
    assert isinstance(model, RecordingModel)
    assert model.max_tokens[0] == 100 and spent == 15
    assert again.status_code == 200 and record.model_calls == 2


@pytest.mark.parametrize("stream", [False, True])
async def test_an_exhausted_run_is_a_429_json_body_in_the_anthropic_shape(stream: bool) -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = _run(max_tokens=15)
    headers = {"traceparent": TRACEPARENT}
    async with (
        _client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx),
    ):
        assert (await client.post("/v1/messages", json=BODY, headers=headers)).status_code == 200
        res = await client.post("/v1/messages", json={**BODY, "stream": stream}, headers=headers)
    assert res.status_code == 429
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["error"]["type"] == "rate_limit_error"
    assert res.json()["error"]["message"].startswith("budget_exhausted: ")
    assert res.headers["x-should-retry"] == "false"
    assert _telemetry(ports).counter_value("chassis.model_calls_refused", route="big-default") == 1


async def test_the_uncorrelated_cap_is_shared_by_both_routes() -> None:
    ports = _ports()
    app = create_app(
        ChassisConfig.model_validate(
            {
                **CONFIG,
                "spec": {**CONFIG["spec"], "limits": {"uncorrelated_tokens_per_minute": 100}},
            }
        ),
        ports,
    )
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        chat = await client.post("/v1/chat/completions", json={**BODY, "max_tokens": 60})
        messages = await client.post("/v1/messages", json={**BODY, "max_tokens": 60})
    assert chat.status_code == 200
    # 15 spent on chat, 60 asked here: 75 fits; the cap is 100 per minute, so ask for more.
    assert messages.status_code == 200
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        refused = await client.post("/v1/messages", json={**BODY, "max_tokens": 90})
        back = await client.post("/v1/chat/completions", json={**BODY, "max_tokens": 90})
    assert refused.status_code == 429 and back.status_code == 429
    assert refused.json()["error"]["message"].startswith("budget_exhausted: ")


async def test_no_header_reaches_the_model_and_a_query_is_ignored() -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    headers = {
        "authorization": "Bearer should-not-travel",
        "x-api-key": "should-not-travel",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "x",
    }
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages?beta=true&x=1", json=BODY, headers=headers)
    assert res.status_code == 200
    assert "should-not-travel" not in repr(_telemetry(ports).spans) + res.text + repr(res.headers)


async def test_the_proxy_is_503_until_ready() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    async with _client(create_proxy_app(app)) as client:
        res = await client.post("/v1/messages", json=BODY)
    assert res.status_code == 503
    assert res.json()["error"]["type"] == "overloaded_error"
    assert res.headers["x-should-retry"] == "true"


@pytest.mark.parametrize(
    ("code", "retryable", "status", "kind", "named"),
    [
        ("http_429", True, 429, "rate_limit_error", "model_rate_limited"),
        ("http_503", True, 503, "overloaded_error", "model_overloaded"),
        ("http_529", True, 503, "overloaded_error", "model_overloaded"),
        ("timeout", True, 504, "timeout_error", "model_timeout"),
        ("http_500", True, 502, "api_error", "model_unavailable"),
        ("bad_response", False, 500, "api_error", "model_error"),
    ],
)
async def test_the_model_error_rows_send_fixed_text(
    code: str, retryable: bool, status: int, kind: str, named: str
) -> None:
    model = FailingModel(ModelError(code, "UPSTREAM-RAW-TEXT", retryable=retryable))
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    async with _client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=BODY)
    assert res.status_code == status
    assert res.json()["error"]["type"] == kind
    assert res.json()["error"]["message"].startswith(f"{named}: ")
    assert "UPSTREAM-RAW-TEXT" not in res.text
    assert res.headers["x-should-retry"] == ("true" if retryable else "false")
