"""Shared setup for the echo-openai-agents tests. Not a test module.

The model is the fake model server on `scripts/bakeoff.yaml`, the tools are `fake_mcp_server` with
the two read-only tools of the bake-off (`glossary_lookup`, `acronym_expand`), and both run over
`httpx2.ASGITransport`, so there is no socket. The hooks `echo_openai_agents.handle.transport` and
`echo_openai_agents.tools.transport` carry the traffic. A `Recording` transport keeps every request
(headers included). The chassis is never imported; the event schema is read by path.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import jsonschema
import pytest
from fake_mcp_server.server import FakeMcpState, create_app
from fake_model_server import Script
from fake_model_server import create_app as create_model_app

ROOT = Path(__file__).resolve().parents[4]
BAKEOFF_SCRIPT = ROOT / "packages/fake-model-server/scripts/bakeoff.yaml"
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())
MODEL_URL = "http://model.invalid/v1"
TOOL_URL = "http://tools.invalid/mcp/"
"""The fake MCP app serves `/mcp/`; the trailing slash avoids a redirect."""
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
TOKEN = "tok-remote-0123456789"
PLANTED = "sk-planted-do-not-leak"

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

SMOKE = "simplify: Hello."
SIMPLIFIER = "simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent."
LOOKUP = "lookup: Define SLM and expand RAG."
TWO_TOOLS = frozenset({"glossary_lookup", "acronym_expand"})

# `handle.py` is shadowed by the function `echo_openai_agents.handle`; the modules hold the hooks.
handle_module: Any = importlib.import_module("echo_openai_agents.handle")
tools_module: Any = importlib.import_module("echo_openai_agents.tools")
handle = handle_module.handle


class Recording(httpx2.AsyncBaseTransport):
    """Delegates to an inner transport and keeps every request it saw, headers included."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


@dataclass
class Stubs:
    model_app: Any
    model: Recording
    tools: Recording
    tool_state: FakeMcpState

    def all_requests(self) -> list[httpx2.Request]:
        return [*self.model.requests, *self.tools.requests]

    @property
    def model_bodies(self) -> list[dict[str, Any]]:
        """The chat request bodies the model server received, oldest first."""
        return [dict(b) for b in self.model_app.state.calls]

    @property
    def tool_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [(str(c["tool"]), dict(c["arguments"])) for c in self.tool_state.calls]


@asynccontextmanager
async def _lifespan(app: Any) -> AsyncIterator[None]:
    """Run the app's lifespan in one task of its own: pytest-asyncio may tear a fixture down in
    another task than set it up, and anyio's cancel scopes must exit where they entered.
    """
    ready, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    waiter = asyncio.create_task(ready.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        waiter.cancel()
        task.result()
    try:
        yield
    finally:
        stop.set()
        await task


@asynccontextmanager
async def running(
    monkeypatch: pytest.MonkeyPatch,
    *,
    script: Script | None = None,
    tools: frozenset[str] = TWO_TOOLS,
) -> AsyncIterator[Stubs]:
    """Fake model and tool stub wired into the hooks; the env points at the stub URLs. A planted
    `OPENAI_API_KEY` is in the environment, and `CHASSIS_API_TOKEN` is not."""
    monkeypatch.setenv("OPENAI_API_KEY", PLANTED)
    monkeypatch.setenv("CHASSIS_MODEL_URL", MODEL_URL)
    monkeypatch.setenv("CHASSIS_TOOL_URL", TOOL_URL)
    monkeypatch.delenv("CHASSIS_API_TOKEN", raising=False)
    model_app = create_model_app(script or Script.from_yaml(BAKEOFF_SCRIPT))
    state = FakeMcpState(allow={"*": tools})
    mcp_app = create_app(state)
    model = Recording(httpx2.ASGITransport(app=model_app))
    tool_transport = Recording(httpx2.ASGITransport(app=mcp_app))
    monkeypatch.setattr(handle_module, "transport", model)
    monkeypatch.setattr(tools_module, "transport", tool_transport)
    async with _lifespan(mcp_app):
        yield Stubs(model_app=model_app, model=model, tools=tool_transport, tool_state=state)


async def run(text: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(e) async for e in handle({"text": text, "data": {}}, ctx or CTX)]


def check_shape(events: list[dict[str, Any]]) -> None:
    """Every event validates against the v0 schema; `start` first, one `end` or `error` last."""
    for event in events:
        jsonschema.validate(event, EVENTS_SCHEMA)
        assert event["schema_version"] == "0"
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] in ("end", "error")
    assert kinds.count("start") == 1 and kinds.count("end") + kinds.count("error") == 1


def answer(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")


def refuse(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("refused", request=request)
