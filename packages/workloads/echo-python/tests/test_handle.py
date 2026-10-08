"""`handle` against the fake model server over an ASGI transport: the event sequence, the prompt
shape, and the error path. No socket, no key, no chassis import.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from echo_python.handle import PROMPT_VERSION, SYSTEM_PROMPT, handle
from fake_model_server import Script, create_app

# `echo_python.handle` the attribute is the function; the module holds the `transport` hook.
handle_module: Any = importlib.import_module("echo_python.handle")

ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"

CTX: dict[str, Any] = {
    "request_id": "req-1",
    "trace_id": "trace-1",
    "idempotency_key": "idem-1",
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000.0, "timeout_ms": 30000.0},
    "versions": {"chassis": "0.1.0"},
    "model_route": "big-default",
}


@pytest.fixture
def fake_server() -> Iterator[Any]:
    app = create_app(Script.from_yaml(EXAMPLE_SCRIPT))
    handle_module.transport = httpx.ASGITransport(app=app)
    yield app
    handle_module.transport = None


async def _run(text: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [e async for e in handle({"text": text, "data": {}}, ctx or CTX)]


async def test_streams_start_deltas_metrics_end(fake_server: Any) -> None:
    events = await _run("simplify: the quick brown fox")
    assert [e["type"] for e in events][:2] == ["start", "delta"]
    assert [e["type"] for e in events][-2:] == ["metrics", "end"]
    assert all(e["schema_version"] == "0" for e in events)
    assert events[0]["request_id"] == "req-1"
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "Plain words. Short sentences. Same facts."
    metrics = events[-2]
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (42, 9)
    assert metrics["model_route"] == "big-default" and metrics["attempt"] == 1
    assert events[-1]["status"] == "ok"


async def test_input_is_its_own_user_message_not_in_the_system_prompt(fake_server: Any) -> None:
    await _run("simplify: secret facts here")
    body = fake_server.state.calls[-1]
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user"]
    system, user = body["messages"]
    assert system["content"] == SYSTEM_PROMPT
    assert "secret facts" not in system["content"]
    assert user["content"] == "simplify: secret facts here"
    assert body["model"] == "big-default" and body["stream"] is True
    assert PROMPT_VERSION == "simplifier-v1"


async def test_route_falls_back_to_big_default(fake_server: Any) -> None:
    await _run("simplify: x", {**CTX, "model_route": None})
    assert fake_server.state.calls[-1]["model"] == "big-default"
    await _run("simplify: x", {**CTX, "model_route": "local-small"})
    assert fake_server.state.calls[-1]["model"] == "local-small"


async def test_no_authorization_header_is_sent() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text='data: {"choices":[]}\n\ndata: [DONE]\n\n')

    handle_module.transport = httpx.MockTransport(record)
    try:
        events = await _run("x")
    finally:
        handle_module.transport = None
    assert seen and "authorization" not in {k.lower() for k in seen[0].headers}
    assert events[-1]["type"] == "end"


async def test_http_error_becomes_an_error_event(fake_server: Any) -> None:
    events = await _run("fail please")
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "http_500" and events[-1]["retryable"] is True
    assert "scripted failure" in events[-1]["message"]


async def test_connection_error_becomes_a_retryable_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    handle_module.transport = httpx.MockTransport(refuse)
    try:
        events = await _run("x")
    finally:
        handle_module.transport = None
    assert events[-1]["type"] == "error" and events[-1]["code"] == "connect_error"
    assert events[-1]["retryable"] is True


async def test_timeout_becomes_a_retryable_error() -> None:
    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow model", request=request)

    handle_module.transport = httpx.MockTransport(hang)
    try:
        events = await _run("x")
    finally:
        handle_module.transport = None
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "timeout" and events[-1]["retryable"] is True
