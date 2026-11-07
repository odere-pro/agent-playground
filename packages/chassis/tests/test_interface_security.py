"""Inbound security on the public interfaces (code review F3).

`BodyLimit` counts every byte it lets through, whatever `Content-Length` claims (R3-01). The
native 422 sends only `loc`, `msg`, and `type`, never the caller's input (R3-29). Native and
MCP send `public_message(code)`, never the exception text (R3-02, contract v8 item 1). A header id
outside the envelope pattern is 400 `invalid_body` (contract v8 item 3). One proposal is pinned as
a strict xfail: the idempotency store key is not scoped by the caller (R3-06). Offline: ASGI only.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Event, Start
from chassis.core.handle import echo
from chassis.core.inbound import public_message
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.auth import StaticTokens
from chassis.fakes.state import InMemoryState
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.limits import BodyLimit
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.types import Message, Receive, Scope, Send

AGENT = "echo"
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": AGENT, "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
LIMIT = 1024
CHUNK = 512
LEAK = "http://internal:9000 refused"
ALICE = "tok-alice"
BOB = "tok-bob"
TOKENS = {
    ALICE: Principal("user-alice", frozenset({"agent:run"})),
    BOB: Principal("user-bob", frozenset({"agent:run"})),
}


# --- helpers -----------------------------------------------------------------------------------


class _RecordingApp:
    """An ASGI app that answers 200 and records each body chunk it reads."""

    def __init__(self) -> None:
        self.called = False
        self.read: list[bytes] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.called = True
        while True:
            message = await receive()
            self.read.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _http_scope(headers: list[tuple[bytes, bytes]], path: str = "/v1/run") -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("chassis", 80),
    }


def _chunked_receive(chunks: int) -> Receive:
    """`chunks` body messages of `CHUNK` bytes each, the last with `more_body` false."""
    messages: list[Message] = [
        {"type": "http.request", "body": b"x" * CHUNK, "more_body": i < chunks - 1}
        for i in range(chunks)
    ]

    async def receive() -> Message:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    return receive


async def _drive(limit: BodyLimit, scope: Scope, receive: Receive) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await limit(scope, receive, send)
    return sent


async def _ignore(message: Message) -> None:
    """A `send` that drops every message."""


def _app(
    engine: FakeEngine | None = None, *, state: InMemoryState | None = None, **ports: Any
) -> FastAPI:
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState() if state is None else state,
        **ports,
    )
    return create_app(ChassisConfig.model_validate(CONFIG), bundle)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
    ):
        yield client


async def _leaking(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    """A workload that fails with an internal address in its exception text."""
    yield Start(request_id=ctx.request_id)
    raise RuntimeError(LEAK)


def _mcp_client(app: FastAPI) -> Client[Any]:
    """A `fastmcp.Client` on `/v1/mcp` through `httpx2.ASGITransport`: no socket."""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kw: Any,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://chassis",
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kw,
        )

    return Client(StreamableHttpTransport("http://chassis/v1/mcp", httpx_client_factory=factory))


# --- R3-01: the body limit counts every byte ---------------------------------------------------


async def test_body_limit_refuses_with_413_when_a_small_length_hides_a_chunked_body() -> None:
    """Content-Length 1 with a chunked body over the limit is 413, and the app reads nothing."""
    inner = _RecordingApp()
    limit = BodyLimit(inner, limit=LIMIT, public=FastAPI())
    scope = _http_scope([(b"content-length", b"1"), (b"transfer-encoding", b"chunked")])

    sent = await _drive(limit, scope, _chunked_receive(chunks=4))

    assert (sent[0]["status"], inner.called) == (413, False)


async def test_body_limit_passes_the_whole_body_when_the_length_is_honest() -> None:
    """A body at the limit with a true Content-Length reaches the app whole."""
    inner = _RecordingApp()
    limit = BodyLimit(inner, limit=LIMIT, public=FastAPI())
    scope = _http_scope([(b"content-length", str(LIMIT).encode())])

    sent = await _drive(limit, scope, _chunked_receive(chunks=LIMIT // CHUNK))

    assert (sent[0]["status"], b"".join(inner.read)) == (200, b"x" * LIMIT)


async def test_body_limit_replays_one_joined_message_when_the_body_came_in_chunks() -> None:
    """A body sent in many chunks reaches the app as one message, so memory is the body's size."""
    inner = _RecordingApp()
    limit = BodyLimit(inner, limit=LIMIT, public=FastAPI())
    scope = _http_scope([(b"transfer-encoding", b"chunked")])

    await _drive(limit, scope, _chunked_receive(chunks=LIMIT // CHUNK))

    assert inner.read == [b"x" * LIMIT]


async def test_body_limit_replays_the_chunks_then_the_disconnect_when_the_client_leaves() -> None:
    """A client that leaves mid-body: the app reads the chunks so far, then the disconnect."""
    messages: list[Message] = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.disconnect"},
    ]
    seen: list[Message] = []

    async def receive() -> Message:
        return messages.pop(0)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.extend([await receive(), await receive()])

    await BodyLimit(app, limit=LIMIT, public=FastAPI())(
        scope=_http_scope([]), receive=receive, send=_ignore
    )

    assert seen == [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.disconnect"},
    ]


async def test_body_limit_refuses_without_reading_when_the_length_is_over() -> None:
    """A Content-Length over the limit is 413 before any body is received."""
    inner = _RecordingApp()
    limit = BodyLimit(inner, limit=LIMIT, public=FastAPI())
    scope = _http_scope([(b"content-length", str(LIMIT + 1).encode())])
    received: list[Message] = []

    async def receive() -> Message:
        message: Message = {"type": "http.request", "body": b"x", "more_body": True}
        received.append(message)
        return message

    sent = await _drive(limit, scope, receive)

    assert (sent[0]["status"], received, inner.called) == (413, [], False)


# --- R3-29: the native 422 does not echo the input ---------------------------------------------


async def test_native_422_has_only_loc_msg_and_type_when_the_budget_is_negative() -> None:
    """Each 422 detail item on /v1/run has only loc, msg, and type."""
    body = {"input": {"text": "secret-input"}, "budget": {"max_tokens": -1}}

    async with _client(_app()) as client:
        reply = await client.post("/v1/run", json=body)

    assert (reply.status_code, [sorted(item) for item in reply.json()["detail"]]) == (
        422,
        [["loc", "msg", "type"]],
    )


async def test_native_422_does_not_echo_the_input_when_the_body_has_the_wrong_type() -> None:
    """A wrongly typed field on /v1/run is a 422 that does not repeat the caller's value."""
    body = {"input": {"text": "hi"}, "stream": "secret-value"}

    async with _client(_app()) as client:
        reply = await client.post("/v1/run", json=body)

    assert (reply.status_code, "secret-value" in reply.text) == (422, False)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/chat/completions", {"model": AGENT, "messages": "not-a-list"}),
        ("/v1/messages", {"model": AGENT, "max_tokens": 64, "messages": "not-a-list"}),
    ],
)
async def test_chat_validation_error_stays_400_invalid_body_when_the_body_is_wrong(
    path: str, body: dict[str, Any]
) -> None:
    """The OpenAI and Anthropic validation errors keep their 400 shape."""
    async with _client(_app()) as client:
        reply = await client.post(path, json=body)

    assert (reply.status_code, reply.json()["error"]["type"]) == (400, "invalid_request_error")


# --- R3-02: native and MCP send the public text, never the exception text ----------------------


async def test_native_run_hides_the_exception_text_when_the_workload_raises() -> None:
    """A workload's exception text, with an internal address, does not reach the native client."""
    app = _app(FakeEngine(handle=_leaking))

    async with _client(app) as client:
        reply = await client.post("/v1/run", json={"input": {"text": "hi"}})

    assert "internal:9000" not in reply.text


def _sse_frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    """`(event, data)` of each SSE frame."""
    frames = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


async def test_native_stream_and_complete_send_the_public_text_and_log_the_raw_text() -> None:
    """Contract v8, item 1: `output.error.message` and the stream's `error` frame hold
    `public_message(code)`; the raw text stays in the log."""
    app = _app(FakeEngine(handle=_leaking))
    body = {"input": {"text": "hi"}}

    async with _client(app) as client:
        complete = await client.post("/v1/run", json=body)
        streamed = await client.post("/v1/run", json={**body, "stream": True})

    public = public_message("engine_error")
    frames = dict(_sse_frames(streamed.text))
    logged = [
        log["detail"] for log in app.state.ports.telemetry.logs if log["message"] == "run error"
    ]
    assert complete.json()["output"]["error"]["message"] == public
    assert frames["error"]["message"] == public
    assert frames["response"]["output"]["error"]["message"] == public
    assert "internal:9000" not in streamed.text
    assert len(logged) == 2 and all("internal:9000" in detail for detail in logged), logged


async def test_mcp_tool_call_sends_the_public_text_when_the_workload_raises() -> None:
    """`tools/call` on `/v1/mcp` answers the envelope `/v1/run` gave: the public text."""
    app = _app(FakeEngine(handle=_leaking))

    async with app.router.lifespan_context(app), _mcp_client(app) as mcp:
        result = await mcp.call_tool(AGENT, {"input": {"text": "hi"}}, raise_on_error=False)

    envelope = result.structured_content
    assert envelope is not None
    assert envelope["output"]["error"]["message"] == public_message("engine_error")
    assert "internal:9000" not in json.dumps(envelope)


# --- contract v8, item 3: a header id outside the envelope pattern is 400 invalid_body -------

HEADER_ID_CALLS: dict[str, tuple[str, dict[str, Any]]] = {
    "native": ("/v1/run", {"input": {"text": "hi"}}),
    "openai": (
        "/v1/chat/completions",
        {"model": AGENT, "messages": [{"role": "user", "content": "hi"}]},
    ),
    "anthropic": (
        "/v1/messages",
        {"model": AGENT, "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
    ),
}


@pytest.mark.parametrize("interface", sorted(HEADER_ID_CALLS))
async def test_an_idempotency_key_header_with_a_newline_is_400_invalid_body_on_every_interface(
    interface: str,
) -> None:
    """The `Idempotency-Key` header is checked against `KEY_PATTERN` before the run; the answer
    names the field and never echoes the value."""
    path, body = HEADER_ID_CALLS[interface]
    app = _app(state=InMemoryState())

    async with _client(app) as client:
        reply = await client.post(path, json=body, headers={"Idempotency-Key": "k-secret\nX"})

    text = reply.json()["detail"] if interface == "native" else reply.json()["error"]["message"]
    assert reply.status_code == 400, reply.text
    assert "idempotency_key" in text
    assert "k-secret" not in reply.text
    if interface != "native":
        assert "invalid_body" in reply.text


# --- R3-06 proposal: the idempotency store key is not scoped by the caller ----------------------


async def test_idempotency_does_not_replay_another_callers_run_when_the_key_is_reused() -> None:
    """Bob, reusing Alice's Idempotency-Key with the same input, does not get her stored run."""
    app = _app(auth=StaticTokens(TOKENS), state=InMemoryState())
    body = {"input": {"text": "hello"}}

    async with _client(app) as client:
        first = await client.post(
            "/v1/run",
            json=body,
            headers={"Authorization": f"Bearer {ALICE}", "Idempotency-Key": "k-shared"},
        )
        second = await client.post(
            "/v1/run",
            json=body,
            headers={"Authorization": f"Bearer {BOB}", "Idempotency-Key": "k-shared"},
        )

    assert first.status_code == 200
    assert second.json()["request_id"] != first.json()["request_id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("request_id", "a\nb"), ("trace_id", "t" * 129), ("idempotency_key", "k y")],
)
async def test_a_native_body_id_outside_the_pattern_is_422(field: str, value: str) -> None:
    """`RunRequest` carries the envelope's id types, so a bad body id is FastAPI's 422, and the
    value is not echoed."""
    app = _app(state=InMemoryState())

    async with _client(app) as client:
        reply = await client.post("/v1/run", json={"input": {"text": "hi"}, field: value})

    assert reply.status_code == 422, reply.text
    assert reply.json()["detail"][0]["loc"] == ["body", field]
    assert value not in reply.text
