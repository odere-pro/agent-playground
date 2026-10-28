"""Shared support for the echo-smolagents tests. Not a test module; the test files import it.

Offline, no key, no chassis import. The fake model server and a FastMCP stub of the chassis tools
are served on Unix sockets (allowed offline) in the test's own event loop. `handle` reaches them
through the two transport hooks: a sync `httpx2.HTTPTransport(uds=...)` for the model (smolagents
is sync) and an async one for MCP. Both are wrapped to record every request, headers included.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import jsonschema
import pytest
import uvicorn
from fake_model_server import Script, create_app
from fastmcp import FastMCP

handle_module: Any = importlib.import_module("echo_smolagents.handle")
tools_module: Any = importlib.import_module("echo_smolagents.tools")

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = Path(__file__).resolve().parent / "scripts/smolagents.yaml"
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
PLANTED = "planted-openai-key-0123456789"
TOKEN = "tok-remote-0123456789"
SLM_DEFINITION = "A small language model (stub)."
RAG_EXPANSION = "retrieval-augmented generation"

SMOKE = "simplify: Hello."
SIMPLIFIER = "simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent."
LOOKUP = "lookup: Define SLM and expand RAG."

CTX: dict[str, Any] = {
    "request_id": "req-1",
    "trace_id": "0af7651916cd43dd8448eb211c80319c",
    "idempotency_key": "idem-1",
    "agent": "echo",
    "agent_version": "0.0.1",
    "budget": {"max_tokens": 2000, "timeout_ms": 30000},
    "versions": {"chassis": "0.1.0"},
    "model_route": "big-default",
    "traceparent": TRACEPARENT,
}

ROUTER_KEEPS = frozenset({"model", "messages", "temperature", "max_tokens", "tools", "stream"})
"""What the chassis model proxy forwards from a chat request body; everything else is dropped."""


class RecordingSync(httpx2.BaseTransport):
    """Delegates to an inner sync transport and keeps every request it saw, headers included."""

    def __init__(self, inner: httpx2.BaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx2.Request] = []

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.inner.handle_request(request)


class RouterFilter(httpx2.BaseTransport):
    """Acts like the chassis model proxy: forwards a chat body with only `ROUTER_KEEPS` keys."""

    def __init__(self, inner: httpx2.BaseTransport) -> None:
        self.inner = inner
        self.received: list[dict[str, Any]] = []
        self.forwarded: list[dict[str, Any]] = []

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.received.append(body)
        kept = {k: v for k, v in body.items() if k in ROUTER_KEEPS}
        self.forwarded.append(kept)
        content = json.dumps(kept).encode()
        headers = {k: v for k, v in request.headers.items() if k.lower() != "content-length"}
        forwarded = httpx2.Request(request.method, request.url, headers=headers, content=content)
        return self.inner.handle_request(forwarded)


class RecordingAsync(httpx2.AsyncBaseTransport):
    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


@dataclass
class Stubs:
    model_app: Any
    model: RecordingSync
    tools: RecordingAsync
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    model_socket: str = ""

    def all_requests(self) -> list[httpx2.Request]:
        return [*self.model.requests, *self.tools.requests]

    def bodies(self) -> list[dict[str, Any]]:
        return list(self.model_app.state.calls)


def tool_stub(calls: list[tuple[str, dict[str, Any]]]) -> FastMCP:
    """The chassis's two read-only tools, plus one that always fails."""
    server: FastMCP = FastMCP(name="chassis-tools-stub")

    @server.tool
    def glossary_lookup(term: str) -> dict[str, str]:
        """Look up a platform term."""
        calls.append(("glossary_lookup", {"term": term}))
        return {"term": term, "definition": SLM_DEFINITION}

    @server.tool
    def acronym_expand(acronym: str) -> dict[str, str | None]:
        """Expand an acronym."""
        calls.append(("acronym_expand", {"acronym": acronym}))
        return {"acronym": acronym, "expansion": RAG_EXPANSION if acronym == "RAG" else None}

    @server.tool
    def explode() -> dict[str, str]:
        """Always fails."""
        raise ValueError("the tool broke")

    return server


@asynccontextmanager
async def serve_uds(app: Any, *, lifespan: str = "off") -> AsyncIterator[str]:
    """Serve `app` over a fresh Unix socket; yields the path. Short on purpose (macOS caps 104)."""
    folder = Path(tempfile.mkdtemp(prefix="sm-"))
    path = str(folder / "s.sock")
    server = uvicorn.Server(uvicorn.Config(app, uds=path, log_level="warning", lifespan=lifespan))  # type: ignore[arg-type]
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("uvicorn exited before it started")
                await asyncio.sleep(0.01)
        yield path
    finally:
        server.should_exit = True
        await task
        shutil.rmtree(folder, ignore_errors=True)


@asynccontextmanager
async def stub_servers(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Stubs]:
    """The fake model and the tool stub on Unix sockets, the hooks pointed at them. A planted
    `OPENAI_API_KEY` is in the environment; `CHASSIS_API_TOKEN` is not.
    """
    monkeypatch.setenv("OPENAI_API_KEY", PLANTED)
    monkeypatch.delenv("CHASSIS_API_TOKEN", raising=False)
    model_app = create_app(Script.from_yaml(SCRIPT))
    calls: list[tuple[str, dict[str, Any]]] = []
    mcp_app = tool_stub(calls).http_app(path="/mcp", stateless_http=True)
    async with serve_uds(model_app) as model_sock, serve_uds(mcp_app, lifespan="on") as tool_sock:
        model = RecordingSync(httpx2.HTTPTransport(uds=model_sock))
        tools = RecordingAsync(httpx2.AsyncHTTPTransport(uds=tool_sock))
        monkeypatch.setattr(handle_module, "transport", model)
        monkeypatch.setattr(tools_module, "transport", tools)
        yield Stubs(
            model_app=model_app, model=model, tools=tools, tool_calls=calls, model_socket=model_sock
        )


async def run(text: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [e async for e in handle_module.handle({"text": text, "data": {}}, ctx or CTX)]


def check_shape(events: list[dict[str, Any]]) -> None:
    """Events validate against events.v0.json; one `start` first, one `end` or `error` last."""
    for event in events:
        jsonschema.validate(event, EVENTS_SCHEMA)
        assert event["schema_version"] == "0"
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] in ("end", "error")
    assert kinds.count("start") == 1 and kinds.count("end") + kinds.count("error") == 1


def text_of(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")
