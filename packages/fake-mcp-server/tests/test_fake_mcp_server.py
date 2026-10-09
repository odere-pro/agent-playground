"""The fake MCP server, in process over an ASGI transport: no socket, no network."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from fake_mcp_server import PROBE_MARKER, FakeMcpState, create_app
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from starlette.applications import Starlette

TOOLS = {"glossary_lookup", "acronym_expand", "note_write", "unlisted_probe"}


@asynccontextmanager
async def connect(app: Starlette, token: str | None = None) -> AsyncIterator[Client[Any]]:
    def factory(**kwargs: Any) -> httpx2.AsyncClient:  # FastMCP passes several keywords
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

    headers = {"Authorization": f"Bearer {token}"} if token else None
    transport = StreamableHttpTransport(
        "http://fake/mcp/",
        headers=headers,
        httpx_client_factory=factory,  # type: ignore[arg-type]
    )
    async with app.router.lifespan_context(app), Client(transport) as client:
        yield client


async def test_lists_four_tools_with_read_only_hints() -> None:
    app = create_app(FakeMcpState())
    async with connect(app) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == TOOLS
    hints = {n: t.annotations.read_only_hint for n, t in tools.items() if t.annotations}
    assert hints["glossary_lookup"] is True
    assert hints["acronym_expand"] is True
    assert hints["note_write"] is False


async def test_acronym_expand_answers_and_unknown_is_not_an_error() -> None:
    app = create_app(FakeMcpState())
    async with connect(app) as client:
        hit = await client.call_tool("acronym_expand", {"acronym": "rag"})
        miss = await client.call_tool("acronym_expand", {"acronym": "nope"})
    assert hit.data == {"acronym": "rag", "expansion": "retrieval-augmented generation"}
    assert miss.data == {"acronym": "nope", "expansion": None}


async def test_glossary_lookup_answers_and_unknown_is_not_an_error() -> None:
    app = create_app(FakeMcpState())
    async with connect(app) as client:
        hit = await client.call_tool("glossary_lookup", {"term": "slm"})
        miss = await client.call_tool("glossary_lookup", {"term": "nope"})
    assert "small language model" in hit.data["definition"]
    assert miss.data == {"term": "nope", "definition": None}


async def test_note_write_dedups_by_meta_key_and_counts_real_executions() -> None:
    state = FakeMcpState()
    async with connect(create_app(state)) as client:
        meta = {"idempotency_key": "k1"}
        first = await client.call_tool("note_write", {"text": "a"}, meta=meta)
        again = await client.call_tool("note_write", {"text": "a"}, meta=meta)
        other = await client.call_tool("note_write", {"text": "a"}, meta={"idempotency_key": "k2"})
    assert first.data == again.data
    assert other.data != first.data
    assert state.executions == 2
    assert [n["key"] for n in state.notes] == ["k1", "k2"]


async def test_note_write_takes_the_key_as_an_argument_when_no_meta() -> None:
    state = FakeMcpState()
    async with connect(create_app(state)) as client:
        a = await client.call_tool("note_write", {"text": "a", "idempotency_key": "k"})
        b = await client.call_tool("note_write", {"text": "b", "idempotency_key": "k"})
    assert a.data == b.data
    assert state.executions == 1


async def test_note_write_refuses_a_call_without_a_key() -> None:
    state = FakeMcpState()
    async with connect(create_app(state)) as client:
        with pytest.raises(ToolError, match="idempotency_key_required"):
            await client.call_tool("note_write", {"text": "a"})
    assert state.executions == 0


async def test_unlisted_probe_returns_its_marker_when_nothing_is_allowed_or_denied() -> None:
    async with connect(create_app(FakeMcpState())) as client:
        out = await client.call_tool("unlisted_probe", {})
    assert out.data == PROBE_MARKER


async def test_allow_hides_and_refuses_tools_outside_the_list() -> None:
    state = FakeMcpState(allow={"tok": frozenset({"glossary_lookup"})})
    async with connect(create_app(state), token="tok") as client:
        assert {t.name for t in await client.list_tools()} == {"glossary_lookup"}
        ok = await client.call_tool("glossary_lookup", {"term": "SLM"})  # paired control
        assert ok.data["definition"]
        with pytest.raises(ToolError, match="unknown tool"):
            await client.call_tool("unlisted_probe", {})
        with pytest.raises(ToolError, match="unknown tool"):
            await client.call_tool("note_write", {"text": "x"}, meta={"idempotency_key": "k"})
    assert state.executions == 0
    assert all(PROBE_MARKER not in str(c) for c in state.calls)


async def test_allow_gives_each_token_its_own_list_and_unknown_tokens_get_none() -> None:
    state = FakeMcpState(
        allow={
            "a": frozenset({"glossary_lookup"}),
            "b": frozenset({"glossary_lookup", "note_write"}),
        }
    )
    app = create_app(state)
    async with connect(app, token="b") as client:
        assert {t.name for t in await client.list_tools()} == {"glossary_lookup", "note_write"}
    async with connect(app, token="zzz") as client:
        assert await client.list_tools() == []
    async with connect(app) as client:
        assert await client.list_tools() == []


async def test_calls_endpoint_lists_calls_only_when_enabled() -> None:
    state = FakeMcpState()
    async with connect(create_app(state, expose_calls=True)) as client:
        await client.call_tool("glossary_lookup", {"term": "SLM"})
    app = create_app(state, expose_calls=True)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://f"
    ) as h:
        body = (await h.get("/calls")).json()
    assert body["executions"] == 0
    assert body["calls"][0]["tool"] == "glossary_lookup"
    off = create_app(state)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=off), base_url="http://f"
    ) as h:
        assert (await h.get("/calls")).status_code == 404
