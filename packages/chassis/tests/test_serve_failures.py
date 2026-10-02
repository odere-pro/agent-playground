"""`serve` when something breaks: an unexpected exception after the run opens, a client that leaves,
and the error text a client sees (PoC-3 review round).

- An exception that is not a run `error` event (the adapter's `complete` raising, telemetry
  failing, a reader exception from the hold rule) is `adapter.error("internal_error", ...)`, a 500
  with `x-should-retry: false`: the real SDK clients, with their default retries, run the agent
  once.
- A streaming client that leaves (before the first frame, mid-stream, or while a send is blocked)
  closes the engine's generator, ends the `chassis.run` span, leaves no task behind, and frees the
  trace id. Driven at the ASGI level, so the disconnect is exact.
- The OpenAI and Anthropic interfaces send a fixed message per error code; the exception text stays
  in the log and the span.

No socket: the SDKs talk to the app through `httpx2.ASGITransport`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx
import httpx2
import openai
import pytest
from chassis.adapters.anthropic_compat.inbound import AnthropicInbound
from chassis.adapters.openai_compat.inbound import OpenAIInbound
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Error, Event
from chassis.core.inbound import Reply, public_message, status_for
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.native import NativeInbound
from fastapi import FastAPI

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
KEY = "placeholder-not-a-key"
SECRET_URL = "http://10.0.0.5"
SECRET_KEY = "sk-x"


class _Engine:
    """A handle that counts its runs and records how it was closed."""

    def __init__(self, *events: Event, block: bool = False, raises: Exception | None = None):
        self.events = events
        self.block = block
        self.raises = raises
        self.runs = 0
        self.closed_by: list[str] = []

    async def handle(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        self.runs += 1
        try:
            for event in self.events:
                yield event
            if self.raises is not None:
                raise self.raises
            if self.block:
                await asyncio.Event().wait()
        except GeneratorExit:
            self.closed_by.append("GeneratorExit")
            raise
        except asyncio.CancelledError:
            self.closed_by.append("CancelledError")
            raise


def _app(engine: _Engine) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=engine.handle),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


def _openai(app: FastAPI) -> openai.AsyncOpenAI:
    """The official client with its default retries (2)."""
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
    return openai.AsyncOpenAI(base_url="http://chassis/v1", api_key=KEY, http_client=http)


def _anthropic(app: FastAPI) -> anthropic.AsyncAnthropic:
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
    return anthropic.AsyncAnthropic(base_url="http://chassis", api_key=KEY, http_client=http)


OPENAI_BODY: dict[str, Any] = {"model": "echo", "messages": [{"role": "user", "content": "hi"}]}
ANTHROPIC_BODY: dict[str, Any] = {**OPENAI_BODY, "max_tokens": 64}
PATHS = {
    "openai": ("/v1/chat/completions", OPENAI_BODY),
    "anthropic": ("/v1/messages", ANTHROPIC_BODY),
}


def _raise(*_: Any, **__: Any) -> Reply:
    raise RuntimeError(f"complete failed at {SECRET_URL}")


# --- item 2: no plain 500 ----------------------------------------------------------------------


def test_internal_error_is_500() -> None:
    assert status_for("internal_error", False) == 500


async def test_openai_complete_raising_is_one_run_and_a_500_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(OpenAIInbound, "complete", _raise)
    engine = _Engine(End(output={"text": "ok"}))
    app = _app(engine)
    async with app.router.lifespan_context(app), _openai(app) as client:
        with pytest.raises(openai.InternalServerError) as caught:
            await client.chat.completions.create(**OPENAI_BODY)
    assert engine.runs == 1
    assert caught.value.status_code == 500
    assert caught.value.response.headers["x-should-retry"] == "false"
    assert caught.value.body["code"] == "internal_error"  # type: ignore[index]
    assert SECRET_URL not in caught.value.response.text
    assert len(app.state.runs) == 0
    logs = _telemetry(app).logs
    assert any(SECRET_URL in str(log.get("detail")) for log in logs), logs


async def test_anthropic_complete_raising_is_one_run_and_a_500_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AnthropicInbound, "complete", _raise)
    engine = _Engine(End(output={"text": "ok"}))
    app = _app(engine)
    async with app.router.lifespan_context(app), _anthropic(app) as client:
        with pytest.raises(anthropic.InternalServerError) as caught:
            await client.messages.create(**ANTHROPIC_BODY)
    assert engine.runs == 1
    assert caught.value.status_code == 500
    assert caught.value.response.headers["x-should-retry"] == "false"
    assert caught.value.body["error"]["type"] == "api_error"  # type: ignore[index]
    assert SECRET_URL not in caught.value.response.text
    assert len(app.state.runs) == 0


async def test_native_complete_raising_keeps_the_native_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(NativeInbound, "complete", _raise)
    engine = _Engine(End(output={"text": "ok"}))
    app = _app(engine)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://c") as client,
    ):
        response = await client.post("/v1/run", json={"input": {"text": "x"}})
    assert response.status_code == 500
    assert response.json() == {"detail": public_message("internal_error")}
    assert len(app.state.runs) == 0


class _BrokenTelemetry(InMemoryTelemetry):
    def counter(self, name: str, value: int = 1, **labels: Any) -> None:
        if name == "chassis.requests":
            raise RuntimeError("telemetry down")
        super().counter(name, value, **labels)


@pytest.mark.parametrize("stream", [False, True])
async def test_a_telemetry_error_in_the_run_is_a_500_internal_error_once(stream: bool) -> None:
    """In stream mode the error is raised in the hold rule's reader task and re-raised by `hold`."""
    engine = _Engine(Delta(text="a"), End(output={"text": "a"}))
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=engine.handle),
        config=InMemoryConfig(),
        telemetry=_BrokenTelemetry(),
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with app.router.lifespan_context(app), _openai(app) as client:
        with pytest.raises(openai.InternalServerError) as caught:
            await client.chat.completions.create(**OPENAI_BODY, stream=stream)
    assert caught.value.response.headers["x-should-retry"] == "false"
    assert engine.runs == 0, "the counter fails before the engine runs"
    assert len(app.state.runs) == 0


@pytest.mark.parametrize("interface", ["openai", "anthropic"])
async def test_stream_raising_synchronously_closes_the_run(
    interface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = OpenAIInbound if interface == "openai" else AnthropicInbound
    monkeypatch.setattr(adapter, "stream", _raise)
    engine = _Engine(Delta(text="a"), block=True)
    app = _app(engine)
    before = asyncio.all_tasks()
    path, body = PATHS[interface]
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://c") as client,
    ):
        response = await client.post(path, json={**body, "stream": True})
    assert response.status_code == 500
    assert response.headers["x-should-retry"] == "false"
    assert len(app.state.runs) == 0
    await _settle()
    assert engine.closed_by, "the engine generator was not closed"
    assert asyncio.all_tasks() - before - {asyncio.current_task()} == set()


# --- item 3: a client that leaves --------------------------------------------------------------


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def _scope(path: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"chassis"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("chassis", 80),
    }


async def _leave(app: FastAPI, path: str, body: dict[str, Any], when: str) -> list[dict[str, Any]]:
    """Call `app` at the ASGI level and leave: `start` while the status line's send is blocked,
    `mid` after the first frame, `blocked` while the first frame's send is blocked.
    """
    sent: list[dict[str, Any]] = []
    left = asyncio.Event()
    blocked = asyncio.Event()
    request = {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    asked = 0

    async def receive() -> dict[str, Any]:
        nonlocal asked
        asked += 1
        if asked == 1:
            return request
        await left.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        start = message["type"] == "http.response.start"
        frame = message["type"] == "http.response.body" and message.get("body")
        if (when == "start" and start) or (when == "blocked" and frame):
            blocked.set()
            left.set()
            await asyncio.Event().wait()  # never returns; the disconnect cancels it
        sent.append(message)
        if when == "mid" and frame:
            left.set()

    asgi: Any = app
    await asyncio.wait_for(asgi(_scope(path), receive, send), timeout=5)
    return sent


@pytest.mark.parametrize("when", ["start", "mid", "blocked"])
@pytest.mark.parametrize("interface", ["openai", "anthropic"])
async def test_a_stream_client_that_leaves_closes_the_run(interface: str, when: str) -> None:
    engine = _Engine(Delta(text="a"), block=True)
    app = _app(engine)
    path, body = PATHS[interface]
    async with app.router.lifespan_context(app):
        before = asyncio.all_tasks()
        sent = await _leave(app, path, {**body, "stream": True}, when)
        await _settle()
        leftover = asyncio.all_tasks() - before
        runs = len(app.state.runs)
    frames = [m for m in sent if m["type"] == "http.response.body" and m.get("body")]
    assert (len(frames) > 0) == (when == "mid"), frames
    assert engine.runs == 1
    assert engine.closed_by, "the engine generator was not closed"
    spans = [s for s in _telemetry(app).spans if s.name == "chassis.run"]
    assert [s.ended for s in spans] == [True]
    assert leftover == set(), leftover
    assert runs == 0, "the trace id is still held"


# --- item 4: client-facing error text ----------------------------------------------------------


def _leaks(text: str) -> bool:
    return SECRET_URL in text or SECRET_KEY in text or "10.0.0.5" in text


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("interface", ["openai", "anthropic"])
@pytest.mark.parametrize("after_delta", [False, True])
async def test_an_engine_exception_text_never_reaches_the_client(
    interface: str, stream: bool, after_delta: bool
) -> None:
    head = (Delta(text="a"),) if after_delta else ()
    engine = _Engine(*head, raises=RuntimeError(f"{SECRET_URL} {SECRET_KEY}"))
    app = _app(engine)
    path, body = PATHS[interface]
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://c") as client,
    ):
        response = await client.post(path, json={**body, "stream": stream})
    assert "engine_error" in response.text or response.status_code == 500
    assert public_message("engine_error") in response.text, response.text
    assert not _leaks(response.text), response.text
    telemetry = _telemetry(app)
    assert any(SECRET_URL in str(log.get("detail")) for log in telemetry.logs)
    (span,) = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert SECRET_URL in str(span.attributes.get("error.message"))


@pytest.mark.parametrize("interface", ["openai", "anthropic"])
async def test_a_connector_error_message_is_replaced_by_the_fixed_text(interface: str) -> None:
    engine = _Engine(Error(code="a2a.transport", message=f"connect_error: {SECRET_URL}"))
    app = _app(engine)
    path, body = PATHS[interface]
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://c") as client,
    ):
        response = await client.post(path, json=body)
    assert response.status_code == 502
    assert public_message("a2a.transport") in response.text
    assert not _leaks(response.text), response.text


async def test_native_keeps_the_engine_message_contract_v1() -> None:
    engine = _Engine(raises=RuntimeError("boom"))
    app = _app(engine)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://c") as client,
    ):
        response = await client.post("/v1/run", json={"input": {"text": "x"}})
    assert response.json()["output"]["error"]["message"] == "RuntimeError: boom"
