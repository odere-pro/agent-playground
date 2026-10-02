"""The remote lane: with `CHASSIS_API_TOKEN` set, every model and MCP request carries
`Authorization: Bearer <token>`; unset, none does; the token never reaches a log record or an event.
No socket, no chassis import.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
from echo_pydanticai import handle
from fake_model_server import Script, create_app
from fastmcp import FastMCP

handle_module: Any = importlib.import_module("echo_pydanticai.handle")
EXAMPLE_SCRIPT = (
    Path(__file__).resolve().parents[4] / "packages/fake-model-server/scripts/example.yaml"
)
TOKEN = "tok-remote-0123456789"
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
CTX: dict[str, Any] = {"request_id": "req-1", "model_route": "big-default"}


class Recording(httpx2.AsyncBaseTransport):
    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


@asynccontextmanager
async def _lifespan(app: Any) -> AsyncIterator[None]:
    ready, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    await ready.wait()
    try:
        yield
    finally:
        stop.set()
        await task


@pytest.fixture
async def stubs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Recording, Recording]]:
    server: FastMCP = FastMCP(name="chassis-tools-stub")

    @server.tool
    def glossary_lookup(term: str) -> dict[str, str]:
        """Look up a platform term."""
        return {"term": term, "definition": "A small language model."}

    mcp_app = server.http_app(path="/mcp", stateless_http=True)
    model = Recording(httpx2.ASGITransport(app=create_app(Script.from_yaml(EXAMPLE_SCRIPT))))
    tools = Recording(httpx2.ASGITransport(app=mcp_app))
    monkeypatch.setattr(handle_module, "model_transport", model)
    monkeypatch.setattr(handle_module, "tool_transport", tools)
    async with _lifespan(mcp_app):
        yield model, tools


async def _run(ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [e async for e in handle({"text": "glossary: SLM", "data": {}}, ctx or CTX)]


async def test_token_set_is_a_bearer_on_every_model_and_tool_request(
    stubs: tuple[Recording, Recording], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    model, tools = stubs
    events = await _run({**CTX, "traceparent": TRACEPARENT})
    assert any(e["type"] == "tool_call" for e in events) and events[-1]["type"] == "end"
    assert len(model.requests) == 2 and tools.requests
    for request in [*model.requests, *tools.requests]:
        assert request.headers.get("authorization") == f"Bearer {TOKEN}", request.url
        assert request.headers.get("traceparent") == TRACEPARENT


@pytest.mark.parametrize("value", [None, ""])
async def test_token_unset_or_empty_sends_no_authorization(
    stubs: tuple[Recording, Recording], monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("CHASSIS_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("CHASSIS_API_TOKEN", value)
    model, tools = stubs
    await _run()
    assert model.requests and tools.requests
    for request in [*model.requests, *tools.requests]:
        assert "authorization" not in request.headers, request.url


async def test_token_is_in_no_log_record_or_event(
    stubs: tuple[Recording, Recording],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    caplog.set_level(logging.DEBUG)
    events = await _run()

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    monkeypatch.setattr(handle_module, "model_transport", httpx2.MockTransport(refuse))
    events += await _run()
    assert events[-1]["type"] == "error"
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(events)
