"""`handle` against the fake model server over an ASGI transport: the event sequence, the prompt
shape, the error path, the `traceparent` header, and the tool loop over MCP against a FastMCP stub
(`httpx2.ASGITransport` through `echo_python.tools.client_factory`). No socket, no key, no chassis
import.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import httpx2
import jsonschema
import pytest
from echo_python import tools
from echo_python.handle import MAX_TOOL_ROUNDS, PROMPT_VERSION, SYSTEM_PROMPT, handle
from fake_model_server import Rule, Script, create_app
from fake_model_server.script import ToolCallSpec
from fastmcp import FastMCP
from starlette.types import ASGIApp

# `echo_python.handle` the attribute is the function; the module holds the `transport` hook.
handle_module: Any = importlib.import_module("echo_python.handle")

ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
DEFINITION = "A small language model."

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


class Recording(httpx.AsyncBaseTransport):
    """The model transport, recording each request's headers before the ASGI app sees it."""

    def __init__(self, app: Any) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.headers: list[httpx.Headers] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        return await self.inner.handle_async_request(request)


class McpStub:
    """A FastMCP stub on `httpx2.ASGITransport`, installed as `tools.client_factory`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.headers: list[httpx2.Headers] = []

    async def _record(self, request: httpx2.Request) -> None:
        self.headers.append(request.headers)

    def factory(self, headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app),
            headers=headers,
            timeout=timeout,
            event_hooks={"request": [self._record]},
        )


def glossary_stub(*, fail: bool = False) -> FastMCP:
    server: FastMCP = FastMCP(name="stub-tools")

    @server.tool
    def glossary_lookup(term: str) -> dict[str, str]:
        """Look up a platform term."""
        if fail:
            raise ValueError("glossary is down")
        return {"term": term, "definition": DEFINITION}

    return server


@pytest.fixture(autouse=True)
def mcp_unreachable() -> Iterator[None]:
    """By default the tool endpoint refuses the connection: tests without a stub run no tools."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    tools.client_factory = lambda headers, timeout: httpx2.AsyncClient(
        transport=httpx2.MockTransport(refuse), headers=headers, timeout=timeout
    )
    yield
    tools.client_factory = tools.default_client_factory


@pytest.fixture
def fake_server() -> Iterator[Any]:
    app = create_app(Script.from_yaml(EXAMPLE_SCRIPT))
    handle_module.transport = Recording(app)
    yield app
    handle_module.transport = None


@asynccontextmanager
async def serving(server: FastMCP) -> AsyncIterator[McpStub]:
    """The stub's lifespan runs inside the test's own task: FastMCP's task group needs that, and
    an async fixture's teardown may run in another task."""
    app = server.http_app(path="/mcp", stateless_http=True)
    async with app.router.lifespan_context(app):
        stub = McpStub(app)
        tools.client_factory = stub.factory
        yield stub


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


def _valid(events: list[dict[str, Any]]) -> None:
    for event in events:
        jsonschema.validate(event, EVENTS_SCHEMA)


async def test_glossary_runs_the_tool_over_mcp_and_answers(fake_server: Any) -> None:
    async with serving(glossary_stub()):
        events = await _run("glossary: what is SLM?", {**CTX, "traceparent": TRACEPARENT})
    _valid(events)
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["start", "tool_call"] and kinds[-2:] == ["metrics", "end"]
    assert set(kinds[2:-2]) == {"delta"}
    call = events[1]
    assert call["name"] == "glossary_lookup" and call["arguments"] == {"term": "SLM"}
    assert call["result"] == {"term": "SLM", "definition": DEFINITION}
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "From the glossary: SLM means small language model."
    metrics = events[-2]
    # Two model calls, the example script's default usage (10, 5) on each.
    assert (metrics["input_tokens"], metrics["output_tokens"]) == (20, 10)

    first, second = fake_server.state.calls
    assert "tools" in first and len(first["messages"]) == 2
    assistant, tool = second["messages"][2:]
    assert assistant["role"] == "assistant" and assistant["content"] is None
    (requested,) = assistant["tool_calls"]
    assert requested["id"] == call["call_id"] and requested["type"] == "function"
    assert requested["function"]["name"] == "glossary_lookup"
    assert json.loads(requested["function"]["arguments"]) == {"term": "SLM"}
    assert tool["role"] == "tool" and tool["tool_call_id"] == call["call_id"]
    assert json.loads(tool["content"]) == call["result"]


async def test_offered_tools_are_the_listed_mcp_tools(fake_server: Any) -> None:
    async with serving(glossary_stub()):
        await _run("glossary: SLM")
    listed = (await glossary_stub().list_tools())[0]
    (offered,) = fake_server.state.calls[0]["tools"]
    assert offered["type"] == "function"
    assert offered["function"]["name"] == listed.name == "glossary_lookup"
    assert offered["function"]["parameters"] == listed.parameters
    assert offered["function"]["description"] == listed.description


async def test_traceparent_is_on_every_model_and_mcp_request(fake_server: Any) -> None:
    async with serving(glossary_stub()) as mcp_stub:
        await _run("glossary: SLM", {**CTX, "traceparent": TRACEPARENT})
    model = handle_module.transport
    assert len(model.headers) == 2 and len(mcp_stub.headers) >= 4
    for headers in [*model.headers, *mcp_stub.headers]:
        assert headers.get("traceparent") == TRACEPARENT
        assert "authorization" not in headers


async def test_no_traceparent_in_ctx_sends_no_header(fake_server: Any) -> None:
    async with serving(glossary_stub()) as mcp_stub:
        await _run("glossary: SLM")
    model = handle_module.transport
    assert model.headers and mcp_stub.headers
    for headers in [*model.headers, *mcp_stub.headers]:
        assert "traceparent" not in headers


async def test_unreachable_tool_endpoint_runs_without_tools(fake_server: Any) -> None:
    before = tools.list_failures
    events = await _run("simplify: x")
    _valid(events)
    assert events[-1]["type"] == "end"
    assert "tools" not in fake_server.state.calls[0]
    assert tools.list_failures == before + 1


async def test_failed_tools_call_is_a_tool_error(fake_server: Any) -> None:
    async with serving(glossary_stub(fail=True)):
        events = await _run("glossary: SLM")
    _valid(events)
    assert [e["type"] for e in events] == ["start", "error"]
    assert events[-1]["code"] == "tool_error" and events[-1]["retryable"] is False
    assert len(fake_server.state.calls) == 1


async def test_tool_loop_is_capped() -> None:
    lookup = ToolCallSpec(name="glossary_lookup", arguments={"term": "SLM"})
    script = Script(rules=[Rule(tool_call=lookup), Rule(after_tool=True, tool_call=lookup)])
    app = create_app(script)
    handle_module.transport = httpx.ASGITransport(app=app)
    try:
        async with serving(glossary_stub()):
            events = await _run("glossary: SLM")
    finally:
        handle_module.transport = None
    _valid(events)
    kinds = [e["type"] for e in events]
    assert kinds == ["start", *["tool_call"] * MAX_TOOL_ROUNDS, "error"]
    assert events[-1]["code"] == "tool_loop_exceeded" and events[-1]["retryable"] is False
    assert len(app.state.calls) == MAX_TOOL_ROUNDS + 1


async def test_plain_run_events_validate_against_the_schema(fake_server: Any) -> None:
    _valid(await _run("simplify: the quick brown fox"))
    _valid(await _run("fail please"))
