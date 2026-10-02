"""The remote lane: with `CHASSIS_API_TOKEN` set, every model and MCP request carries
`Authorization: Bearer <token>`; unset, none does; the token never reaches a log record. No socket,
no chassis import.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from echo_python import tools
from echo_python.handle import handle
from fake_model_server import Script, create_app
from fastmcp import FastMCP

handle_module: Any = importlib.import_module("echo_python.handle")
EXAMPLE_SCRIPT = (
    Path(__file__).resolve().parents[4] / "packages/fake-model-server/scripts/example.yaml"
)
TOKEN = "tok-remote-0123456789"
CTX: dict[str, Any] = {"request_id": "req-1", "model_route": "big-default"}


class Recording(httpx.AsyncBaseTransport):
    def __init__(self, app: Any) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.headers: list[httpx.Headers] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        return await self.inner.handle_async_request(request)


@pytest.fixture
def model() -> Iterator[Recording]:
    recording = Recording(create_app(Script.from_yaml(EXAMPLE_SCRIPT)))
    handle_module.transport = recording
    yield recording
    handle_module.transport = None


@asynccontextmanager
async def mcp() -> AsyncIterator[list[httpx2.Headers]]:
    server: FastMCP = FastMCP(name="stub-tools")

    @server.tool
    def glossary_lookup(term: str) -> dict[str, str]:
        """Look up a platform term."""
        return {"term": term, "definition": "A small language model."}

    seen: list[httpx2.Headers] = []

    async def record(request: httpx2.Request) -> None:
        seen.append(request.headers)

    def factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers=headers,
            timeout=timeout,
            event_hooks={"request": [record]},
        )

    app = server.http_app(path="/mcp", stateless_http=True)
    async with app.router.lifespan_context(app):
        tools.client_factory = factory
        try:
            yield seen
        finally:
            tools.client_factory = tools.default_client_factory


async def _run() -> list[dict[str, Any]]:
    return [e async for e in handle({"text": "glossary: SLM", "data": {}}, CTX)]


async def test_token_set_is_a_bearer_on_every_model_and_tool_request(
    model: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    async with mcp() as tool_headers:
        events = await _run()
    assert any(e["type"] == "tool_call" for e in events) and events[-1]["type"] == "end"
    assert len(model.headers) == 2 and len(tool_headers) >= 4
    for headers in [*model.headers, *tool_headers]:
        assert headers.get("authorization") == f"Bearer {TOKEN}"


async def test_token_set_keeps_the_traceparent(
    model: Recording, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    ctx = {**CTX, "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
    async with mcp() as tool_headers:
        _ = [e async for e in handle({"text": "glossary: SLM", "data": {}}, ctx)]
    for headers in [*model.headers, *tool_headers]:
        assert headers.get("traceparent") == ctx["traceparent"]
        assert headers.get("authorization") == f"Bearer {TOKEN}"


@pytest.mark.parametrize("value", [None, ""])
async def test_token_unset_or_empty_sends_no_authorization(
    model: Recording, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("CHASSIS_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("CHASSIS_API_TOKEN", value)
    async with mcp() as tool_headers:
        await _run()
    assert model.headers and tool_headers
    for headers in [*model.headers, *tool_headers]:
        assert "authorization" not in headers


async def test_token_is_in_no_log_record_or_event(
    model: Recording, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CHASSIS_API_TOKEN", TOKEN)
    caplog.set_level(logging.DEBUG)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with mcp():
        events = await _run()
        handle_module.transport = httpx.MockTransport(refuse)  # an error path, too
        events += await _run()
    assert events[-1]["type"] == "error"
    assert TOKEN not in caplog.text
    assert all(TOKEN not in str(r.getMessage()) for r in caplog.records)
    assert TOKEN not in repr(events)
